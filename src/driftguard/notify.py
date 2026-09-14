"""Outbound notifications: one JSON event per finished run.

DriftGuard's interface is GitHub — the docs PR, the question comment. This is
the hook for everything around it: team chat, e-mail, tickets. The project
routes these events with n8n (docs/n8n-notifications.md), but any HTTP
listener works: the contract is the JSON built below, nothing n8n-specific.

Design rules:
- One event at the END of a run, whatever the outcome. DriftGuard states the
  facts plus a severity — "action" (a human should do something), "alert"
  (something failed), "info" (nothing to do) — and the consumer decides who
  hears about what. Routing lives in the workflow tool, not in this code.
- Fire-and-forget. A notification failure is logged and never changes the
  delivery's status: the archive is the source of truth, the message is a
  courtesy, and a chat outage must not make a docs PR look like an error.
- Built from the same read model as the story page (driftguard/story.py), so
  a message can never disagree with the page it links to.
- Authentication is a shared token in a header, which n8n's Webhook node
  verifies natively (Header Auth). HMAC signing like GitHub's would be
  stronger; it is one Code node away on the consumer side and out of v1.
"""

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime

import httpx

from driftguard.config import Settings
from driftguard.storage import db
from driftguard.story import build_story, story_delivery_id

log = logging.getLogger("driftguard.notify")

EVENT_TYPE = "driftguard.run.finished"
TOKEN_HEADER = "X-DriftGuard-Token"
TIMEOUT_SECONDS = 5.0

# The three-way routing key. Everything not listed is "info".
_SEVERITY = {
    "docs_pr_opened": "action",
    "proposals_ready": "action",
    "questions_open": "action",
    "error": "alert",
    "agent_error": "alert",
    "all_rejected": "alert",
}
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)$")


def severity_for(status: str) -> str:
    return _SEVERITY.get(status, "info")


def build_event(settings: Settings, delivery_id: str) -> dict | None:
    """The notification payload for one delivery, or None if it is unknown."""
    row = db.get_delivery(settings.database_path, delivery_id)
    if row is None:
        return None
    repo, pr_number, status = row["repo"], row["pr_number"], row["status"]
    kind = "answer" if row["event"] == "issue_comment" else "merged_pr"

    # Facts and questions belong to the merge; an answer run reports on the
    # same PR story it continues, and links to that page.
    story_id = delivery_id
    if kind == "answer":
        story_id = story_delivery_id(settings.database_path, repo, pr_number) or delivery_id
    story = build_story(settings.database_path, story_id) or {}
    pull_request = story.get("pull_request") or {}

    if kind == "answer":
        comment = (json.loads(row["payload_json"]).get("comment") or {}).get("user") or {}
        author = comment.get("login")
    else:
        author = pull_request.get("author")

    rows = db.proposals_for_delivery(settings.database_path, delivery_id)
    counts = Counter(r["status"] for r in rows)
    proposals = {key: counts.get(key, 0) for key in ("published", "verified", "rejected", "stale")}
    questions = [
        {
            key: q[key]
            for key in (
                "label",
                "status",
                "doc_path",
                "heading",
                "question",
                "answer",
                "answered_by",
                "comment_url",
            )
        }
        for q in story.get("questions", [])
    ]
    open_questions = [q for q in questions if q["status"] == "open"]
    facts = [
        {key: f[key] for key in ("kind", "symbol", "old", "new", "file")}
        for f in story.get("facts", [])
    ]
    docs_pr_url = row["docs_pr_url"]
    docs_pr_match = _PR_NUMBER_RE.search(docs_pr_url or "")
    docs_pr_number = int(docs_pr_match.group(1)) if docs_pr_match else None
    pr_url = pull_request.get("html_url") or (
        f"https://github.com/{repo}/pull/{pr_number}" if repo and pr_number else None
    )

    return {
        "event": EVENT_TYPE,
        "sent_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "delivery_id": delivery_id,
        "kind": kind,
        "status": status,
        "severity": severity_for(status),
        "repo": repo,
        "pr_number": pr_number,
        "pr_title": pull_request.get("title"),
        "pr_url": pr_url,
        "author": author,
        "docs_pr_url": docs_pr_url,
        "docs_pr_number": docs_pr_number,
        "story_url": f"{settings.public_base_url.rstrip('/')}/ui/deliveries/{story_id}",
        "facts": facts,
        "proposals": proposals,
        "questions": questions,
        "open_questions": len(open_questions),
        "summary": _summary(
            kind, status, repo, pr_number, author, facts, docs_pr_number, open_questions,
            proposals, delivery_id,
        ),
    }


def _summary(
    kind: str,
    status: str,
    repo: str | None,
    pr_number: int | None,
    author: str | None,
    facts: list[dict],
    docs_pr_number: int | None,
    open_questions: list[dict],
    proposals: dict[str, int],
    delivery_id: str,
) -> str:
    """One human sentence a chat message can carry on its own."""
    where = (
        f"PR #{pr_number} on {repo}"
        if kind == "merged_pr"
        else f"Answer on PR #{pr_number} ({repo}) by {author}"
    )
    if status in ("error", "agent_error"):
        return (
            f"{where}: DriftGuard failed ({status}) — replay with:"
            f" python -m driftguard.cli.replay run {delivery_id}"
        )
    if status == "logged":
        return f"{where}: archived only, credentials missing"

    parts: list[str] = []
    if kind == "merged_pr":
        parts.append(f"{len(facts)} drift fact(s)" if facts else "no documentation drift")
    if docs_pr_number:
        parts.append(f"docs PR #{docs_pr_number} opened for review")
    elif proposals.get("verified"):
        parts.append(f"{proposals['verified']} verified fix(es) stored, publishing is off")
    if proposals.get("rejected"):
        parts.append(f"{proposals['rejected']} fix(es) failed verification and were dropped")
    if proposals.get("stale"):
        parts.append(f"{proposals['stale']} section(s) no longer exist in the docs")
    if open_questions:
        labels = ", ".join(q["label"] for q in open_questions)
        parts.append(f"{len(open_questions)} question(s) waiting for a maintainer ({labels})")
    if kind == "answer":
        if status == "no_answers_matched":
            parts.append("the comment matched no open question")
        elif not docs_pr_number and not any(proposals.values()):
            parts.append(
                "the reply did not settle the open question(s)"
                if open_questions
                else "question settled, nothing to change"
            )
    return f"{where}: " + " · ".join(parts)


def send(
    settings: Settings,
    event: dict,
    *,
    url: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """POST one event. Returns True on 2xx; logs and returns False otherwise."""
    target = url or settings.notify_webhook_url
    if not target:
        return False
    headers = {"Content-Type": "application/json"}
    if settings.notify_webhook_token:
        headers[TOKEN_HEADER] = settings.notify_webhook_token
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as http:
            response = http.post(target, content=json.dumps(event), headers=headers)
    except httpx.HTTPError as exc:
        log.warning("notification for %s not delivered: %s", event.get("delivery_id"), exc)
        return False
    if response.status_code >= 400:
        log.warning(
            "notification for %s rejected: HTTP %s %s",
            event.get("delivery_id"),
            response.status_code,
            response.text[:200],
        )
        return False
    log.info(
        "notification sent for %s (%s, %s)",
        event.get("delivery_id"),
        event.get("status"),
        event.get("severity"),
    )
    return True


def notify(
    settings: Settings, delivery_id: str, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Build and send the event for a finished delivery.

    A no-op when NOTIFY_WEBHOOK_URL is empty. Never raises: a bug in reporting
    must not undo a run that already succeeded.
    """
    if not settings.notify_webhook_url:
        return False
    try:
        event = build_event(settings, delivery_id)
    except Exception:
        log.exception("could not build the notification for %s", delivery_id)
        return False
    if event is None:
        return False
    return send(settings, event, transport=transport)
