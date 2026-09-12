"""The story page: what DriftGuard did with one merged pull request, for humans.

Read-only, served by the same FastAPI app, rendered in the browser from two
JSON endpoints. It exists for the demo and for the maintainer who asks "why
did the bot open this PR?" — every line it shows is a row the agent stored
while working: facts, retrieved sections with their ranks, the judge's verdict
on each one (including the sections left alone), fix attempts with the
verifier's feedback, questions and answers. Nothing is recomputed here, so
the page can never disagree with what actually happened.
"""

import difflib
import json
from pathlib import Path
from sqlite3 import Row

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from driftguard.config import Settings
from driftguard.storage import db

STATIC_DIR = Path(__file__).parent / "static"

# The pipeline as the page draws it. A delivery's status maps to the stage it
# is in (index) and whether that stage is running, done, or failed.
STAGES = ("webhook", "facts", "search", "judge", "fix", "result")
FINAL_STATUSES = frozenset(
    {
        "logged",
        "no_drift",
        "docs_pr_opened",
        "proposals_ready",
        "all_rejected",
        "questions_open",
        "error",
        "agent_error",
        "answers_processed",
        "no_answers_matched",
    }
)
_STATUS_STAGE = {
    "received": (1, "running"),
    "logged": (0, "done"),
    "facts_extracted": (2, "running"),
    "searching": (2, "running"),
    "judging": (3, "running"),
    "asking": (3, "running"),
    "fixing": (4, "running"),
    "verifying": (4, "running"),
    "publishing": (5, "running"),
    "no_drift": (1, "done"),
    "docs_pr_opened": (5, "done"),
    "proposals_ready": (5, "done"),
    "all_rejected": (5, "done"),
    "questions_open": (5, "done"),
    "answers_processed": (5, "done"),
    "no_answers_matched": (5, "done"),
    "error": (1, "error"),
    "agent_error": (3, "error"),
}


def diff_lines(old: str, new: str) -> list[dict[str, str]]:
    """Line diff as a flat list of {type: same|del|add, text} for the page."""
    a, b = old.splitlines(), new.splitlines()
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.extend({"type": "same", "text": line} for line in a[i1:i2])
            continue
        if tag in ("replace", "delete"):
            out.extend({"type": "del", "text": line} for line in a[i1:i2])
        if tag in ("replace", "insert"):
            out.extend({"type": "add", "text": line} for line in b[j1:j2])
    return out


def _stage(status: str) -> dict:
    index, state = _STATUS_STAGE.get(status, (0, "running"))
    return {"index": index, "state": state, "status": status, "final": status in FINAL_STATUSES}


def _pull_request(repo: str | None, payload: dict) -> dict:
    pr = payload.get("pull_request") or {}
    user = pr.get("user") or {}
    base_sha = (pr.get("base") or {}).get("sha")
    merge_sha = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    number = payload.get("number")
    html_url = pr.get("html_url") or (f"https://github.com/{repo}/pull/{number}" if repo else None)
    compare_url = (
        f"https://github.com/{repo}/compare/{base_sha}...{merge_sha}"
        if repo and base_sha and merge_sha
        else None
    )
    return {
        "title": pr.get("title"),
        "author": user.get("login"),
        "avatar_url": user.get("avatar_url"),
        "html_url": html_url,
        "files_url": f"{html_url}/files" if html_url else None,
        "merged_at": pr.get("merged_at"),
        "base_sha": base_sha,
        "merge_sha": merge_sha,
        "compare_url": compare_url,
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
    }


def _comment(payload: dict) -> dict:
    comment = payload.get("comment") or {}
    user = comment.get("user") or {}
    return {
        "author": user.get("login"),
        "avatar_url": user.get("avatar_url"),
        "body": comment.get("body"),
        "html_url": comment.get("html_url"),
        "created_at": comment.get("created_at"),
    }


def _facts(repo: str | None, merge_sha: str | None, rows: list[Row]) -> list[dict]:
    return [
        {
            "kind": r["kind"],
            "symbol": r["symbol"],
            "old": r["old"],
            "new": r["new"],
            "file": r["file"],
            "file_url": (
                f"https://github.com/{repo}/blob/{merge_sha}/{r['file']}"
                if repo and merge_sha
                else None
            ),
        }
        for r in rows
    ]


def _proposals(rows: list[Row]) -> list[dict]:
    return [
        {
            "doc_path": r["doc_path"],
            "heading": r["heading"],
            "status": r["status"],
            "facts": r["facts"],
            "reasons": r["reasons"].splitlines(),
            "attempts": r["attempts"],
            "feedback": r["feedback"],
            "lines": r["end_line"] - r["start_line"] + 1,
            "diff": diff_lines(r["old_text"], r["new_text"]),
        }
        for r in rows
    ]


def _questions(repo: str | None, pr_number: int | None, rows: list[Row]) -> list[dict]:
    return [
        {
            "label": r["q_label"],
            "status": r["status"],
            "doc_path": r["doc_path"],
            "heading": r["heading"],
            "question": r["question"],
            "answer": r["answer"],
            "answered_by": r["answered_by"],
            "answered_at": r["answered_at"],
            "created_at": r["created_at"],
            "comment_url": (
                f"https://github.com/{repo}/pull/{pr_number}#issuecomment-{r['comment_id']}"
                if r["comment_id"] and repo
                else None
            ),
        }
        for r in rows
    ]


def _story_delivery(db_path: Path, repo: str | None, pr_number: int | None) -> str | None:
    """The delivery a PR's story page belongs to: the merge itself."""
    if not repo or pr_number is None:
        return None
    for row in db.deliveries_for_pr(db_path, repo, pr_number):
        if row["event"] == "pull_request":
            return row["delivery_id"]
    return None


def build_story(db_path: Path, delivery_id: str) -> dict | None:
    row = db.get_delivery(db_path, delivery_id)
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    repo, pr_number = row["repo"], row["pr_number"]
    pull_request = _pull_request(repo, payload)

    followups = []
    if repo and pr_number is not None:
        for other in db.deliveries_for_pr(db_path, repo, pr_number):
            if other["event"] != "issue_comment":
                continue
            followups.append(
                {
                    "delivery_id": other["delivery_id"],
                    "received_at": other["received_at"],
                    "status": other["status"],
                    "stage": _stage(other["status"]),
                    "docs_pr_url": other["docs_pr_url"],
                    "comment": _comment(json.loads(other["payload_json"])),
                    "proposals": _proposals(
                        db.proposals_for_delivery(db_path, other["delivery_id"])
                    ),
                }
            )

    return {
        "delivery": {
            "id": row["delivery_id"],
            "event": row["event"],
            "action": row["action"],
            "repo": repo,
            "pr_number": pr_number,
            "status": row["status"],
            "received_at": row["received_at"],
            "docs_pr_url": row["docs_pr_url"],
        },
        "stage": _stage(row["status"]),
        "pull_request": pull_request,
        "facts": _facts(
            repo, pull_request["merge_sha"], db.facts_for_delivery(db_path, delivery_id)
        ),
        "verdicts": [dict(r) for r in db.verdicts_for_delivery(db_path, delivery_id)],
        "proposals": _proposals(db.proposals_for_delivery(db_path, delivery_id)),
        "questions": _questions(repo, pr_number, db.questions_for_delivery(db_path, delivery_id)),
        "followups": followups,
    }


def list_stories(db_path: Path, limit: int = 50) -> list[dict]:
    items = []
    for row in db.recent_deliveries(db_path, limit):
        payload = json.loads(row["payload_json"])
        if row["event"] == "issue_comment":
            comment = _comment(payload)
            first_line = (comment["body"] or "").strip().splitlines()
            title = first_line[0][:120] if first_line else "(comment)"
            author = comment["author"]
            story_id = _story_delivery(db_path, row["repo"], row["pr_number"]) or row["delivery_id"]
        else:
            pr = _pull_request(row["repo"], payload)
            title, author, story_id = pr["title"], pr["author"], row["delivery_id"]
        items.append(
            {
                "delivery_id": row["delivery_id"],
                "story_id": story_id,
                "event": row["event"],
                "repo": row["repo"],
                "pr_number": row["pr_number"],
                "status": row["status"],
                "stage": _stage(row["status"]),
                "received_at": row["received_at"],
                "docs_pr_url": row["docs_pr_url"],
                "title": title,
                "author": author,
            }
        )
    return items


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
