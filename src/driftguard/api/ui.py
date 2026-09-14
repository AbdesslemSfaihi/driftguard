"""The story page: what DriftGuard did with one merged pull request, for humans.

Read-only, served by the same FastAPI app, rendered in the browser from two
JSON endpoints over the run story (driftguard/story.py) — the same read model
the notifications use, so the page, the chat message and the archive can
never disagree. Nothing is recomputed here: every line the page shows is a
row the agent stored while working.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from driftguard.config import Settings
from driftguard.story import build_story, list_stories

STATIC_DIR = Path(__file__).parent / "static"


def ui_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/ui", tags=["story page"])

    @router.get("", include_in_schema=False)
    def index_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @router.get("/deliveries/{delivery_id}", include_in_schema=False)
    def story_page(delivery_id: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "delivery.html")

    @router.get("/api/deliveries")
    def api_list(limit: int = 50) -> list[dict]:
        return list_stories(settings.database_path, limit)

    @router.get("/api/deliveries/{delivery_id}")
    def api_story(delivery_id: str) -> dict:
        story = build_story(settings.database_path, delivery_id)
        if story is None:
            raise HTTPException(status_code=404, detail="unknown delivery")
        return story

    return router
