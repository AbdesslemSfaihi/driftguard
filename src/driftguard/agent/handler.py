"""Processing entry point for one merged pull request.

This is the seam between the plumbing (webhook, storage) and the intelligence.
The webhook returns 202 immediately and this runs afterwards, because GitHub
gives a webhook ~10 seconds before it marks the delivery failed and retries —
agent work will take longer than that.

Week 1: log what we received and mark the delivery processed.
Week 2 (Abdesslem): this is where the real work plugs in —
    1. fetch the PR diff via the GitHub API
    2. extract_drift_facts() on the changed Python files   (exercise 01)
    3. invoke the LangGraph agent: search docs -> judge -> fix -> verify -> PR
"""

import json
import logging

from driftguard.config import Settings
from driftguard.storage import db

log = logging.getLogger("driftguard.agent")


def process_delivery(settings: Settings, delivery_id: str) -> None:
    row = db.get_delivery(settings.database_path, delivery_id)
    if row is None:
        log.warning("delivery %s not found in database, nothing to process", delivery_id)
        return

    payload = json.loads(row["payload_json"])
    pr = payload.get("pull_request") or {}
    log.info(
        "merged PR #%s on %s: %r by %s (+%s/-%s in %s files)",
        row["pr_number"],
        row["repo"],
        pr.get("title"),
        (pr.get("user") or {}).get("login"),
        pr.get("additions"),
        pr.get("deletions"),
        pr.get("changed_files"),
    )

    db.set_status(settings.database_path, delivery_id, "logged")
