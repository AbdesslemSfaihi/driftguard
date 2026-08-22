import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from driftguard import __version__
from driftguard.agent.handler import process_delivery
from driftguard.api.security import verify_signature
from driftguard.config import Settings, get_settings
from driftguard.storage import db

log = logging.getLogger("driftguard.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Tests pass their own Settings (temp database, known secret);
    production uses the environment via get_settings()."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="DriftGuard", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
        # Signature check runs on the RAW body, before any parsing: unsigned
        # bytes must never reach json.loads, let alone the agent.
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(body, signature, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="invalid or missing signature")

        delivery_id = request.headers.get("X-GitHub-Delivery")
        if not delivery_id:
            raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery header")

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return {"status": "pong"}

        payload = json.loads(body or b"{}")
        action = payload.get("action")
        pull_request = payload.get("pull_request") or {}

        # DriftGuard only cares about PRs that actually landed. A closed-but-not-
        # merged PR changed nothing, so it is acknowledged and dropped.
        if event != "pull_request" or action != "closed" or not pull_request.get("merged"):
            return {"status": "ignored", "reason": "not a merged pull request"}

        fresh = db.record_delivery(
            settings.database_path,
            delivery_id=delivery_id,
            event=event,
            action=action,
            repo=(payload.get("repository") or {}).get("full_name"),
            pr_number=payload.get("number"),
            payload=payload,
        )
        if not fresh:
            # GitHub redelivered something we already have — succeed loudly, do nothing.
            return {"status": "duplicate", "delivery_id": delivery_id}

        # Respond within GitHub's ~10s webhook deadline; the agent runs after the response.
        background.add_task(process_delivery, settings, delivery_id)
        return {"status": "accepted", "delivery_id": delivery_id}

    return app


app = create_app()
