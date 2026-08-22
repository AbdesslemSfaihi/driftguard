"""Processing entry point for one merged pull request.

This is the seam between the plumbing (webhook, storage) and the intelligence.
The webhook returns 202 immediately and this runs afterwards, because GitHub
gives a webhook ~10 seconds before it marks the delivery failed and retries —
agent work takes longer than that.

Current pipeline (week 1): fetch changed files -> extract drift facts -> persist.
Week 2 plugs the LangGraph agent in after the facts: search docs -> judge ->
fix -> verify -> open the docs PR.
"""

import json
import logging

import httpx

from driftguard.analysis.pipeline import analyze_pr
from driftguard.config import Settings
from driftguard.github.client import GitHubClient, GitHubError
from driftguard.storage import db

log = logging.getLogger("driftguard.agent")


def recover_pending(settings: Settings) -> int:
    """Re-run deliveries a previous process accepted but never finished.

    BackgroundTasks are in-process: a crash between the 202 response and the
    end of process_delivery loses the task — but not the archived payload,
    which stays at status 'received'. Called on startup, this turns the
    archive into a durable inbox: accepted work eventually runs, even across
    restarts. Returns how many deliveries were recovered.
    """
    pending = db.pending_deliveries(settings.database_path)
    for delivery_id in pending:
        log.info("recovering delivery %s left unprocessed by a previous run", delivery_id)
        process_delivery(settings, delivery_id)
    return len(pending)


def process_delivery(settings: Settings, delivery_id: str) -> None:
    row = db.get_delivery(settings.database_path, delivery_id)
    if row is None:
        log.warning("delivery %s not found in database, nothing to process", delivery_id)
        return

    payload = json.loads(row["payload_json"])
    pull_request = payload.get("pull_request") or {}
    log.info(
        "merged PR #%s on %s: %r by %s",
        row["pr_number"],
        row["repo"],
        pull_request.get("title"),
        (pull_request.get("user") or {}).get("login"),
    )

    if not settings.github_token:
        # Keeps the pipeline demoable without credentials (and replayable offline).
        log.info("GITHUB_TOKEN not set — delivery archived, analysis skipped")
        db.set_status(settings.database_path, delivery_id, "logged")
        return

    # base.sha = the commit the PR started from (old truth).
    # merge_commit_sha = what actually landed on the default branch (new truth) —
    # correct even for squash merges, where the PR head commit never lands.
    base_ref = (pull_request.get("base") or {}).get("sha")
    head_ref = pull_request.get("merge_commit_sha") or (pull_request.get("head") or {}).get("sha")
    if not base_ref or not head_ref:
        log.error("delivery %s: payload is missing base/head commit ids", delivery_id)
        db.set_status(settings.database_path, delivery_id, "error")
        return

    try:
        with GitHubClient(settings.github_token) as client:
            facts = analyze_pr(
                client,
                repo=row["repo"],
                number=row["pr_number"],
                base_ref=base_ref,
                head_ref=head_ref,
            )
    except (GitHubError, httpx.TransportError) as exc:
        # Fail loudly into the delivery's status: the replay CLI can re-run it later.
        log.error("delivery %s: GitHub analysis failed: %s", delivery_id, exc)
        db.set_status(settings.database_path, delivery_id, "error")
        return

    db.save_facts(settings.database_path, delivery_id, facts)
    db.set_status(settings.database_path, delivery_id, "facts_extracted")
    log.info(
        "delivery %s: %d drift fact(s): %s",
        delivery_id,
        len(facts),
        [f"{fact.kind}:{fact.symbol}" for fact in facts],
    )
