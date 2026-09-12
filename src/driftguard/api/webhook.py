import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from driftguard import __version__
from driftguard.agent.asking import MAINTAINER_ASSOCIATIONS, QUESTION_MARKER
from driftguard.agent.handler import process_delivery, recover_pending
from driftguard.api.security import verify_signature
from driftguard.api.ui import STATIC_DIR, ui_router
from driftguard.config import Settings, get_settings
from driftguard.storage import db

log = logging.getLogger("driftguard.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Tests pass their own Settings (temp database, known secret);
    production uses the environment via get_settings()."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Deliveries accepted before a crash/restart are still in the archive at
        # status 'received' — re-run them. In a daemon thread so a slow GitHub
        # call can never hold /health (and new webhooks) hostage at startup.
        threading.Thread(
            target=recover_pending, args=(settings,), name="driftguard-recover", daemon=True
        ).start()
        yield

    app = FastAPI(title="DriftGuard", version=__version__, lifespan=lifespan)
    # The story page: read-only, same process, same database (see api/ui.py).
    app.include_router(ui_router(settings))
    app.mount("/ui/static", StaticFiles(directory=str(STATIC_DIR)), name="ui-static")

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

        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            # Correctly signed but not JSON: a buggy sender, not a server bug — 400, not 500.
            raise HTTPException(status_code=400, detail="body is not valid JSON") from exc
        action = payload.get("action")
        pull_request = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")

        if event == "pull_request":
            # DriftGuard only cares about PRs that actually landed. A closed-but-
            # not-merged PR changed nothing, so it is acknowledged and dropped.
            if action != "closed" or not pull_request.get("merged"):
                return {"status": "ignored", "reason": "not a merged pull request"}
            pr_number = payload.get("number")
        elif event == "issue_comment":
            # A comment is only interesting when it might ANSWER one of our open
            # questions: on a PR (issues carry a pull_request key then), not
            # written by us (our digests carry the marker), and only while that
            # PR actually has open questions — everything else is archived
            # nowhere, because it is noise, not work.
            issue = payload.get("issue") or {}
            comment = payload.get("comment") or {}
            if (
                action != "created"
                or "pull_request" not in issue
                or QUESTION_MARKER in (comment.get("body") or "")
                or not repo
                or not db.has_open_questions(settings.database_path, repo, issue.get("number"))
            ):
                return {"status": "ignored", "reason": "not an answer to an open question"}
            # Authority check last, so the log only fires for the interesting
            # case: someone without write access replying to an open question.
            if comment.get("author_association") not in MAINTAINER_ASSOCIATIONS:
                log.info(
                    "ignoring reply on %s#%s by %s (%s): not a maintainer",
                    repo,
                    issue.get("number"),
                    (comment.get("user") or {}).get("login"),
                    comment.get("author_association"),
                )
                return {"status": "ignored", "reason": "commenter is not a maintainer"}
            pr_number = issue.get("number")
        else:
            return {"status": "ignored", "reason": f"event {event!r} is not handled"}

        fresh = db.record_delivery(
            settings.database_path,
            delivery_id=delivery_id,
            event=event,
            action=action,
            repo=repo,
            pr_number=pr_number,
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
