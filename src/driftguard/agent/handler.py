"""Processing entry point for one merged pull request.

This is the seam between the plumbing (webhook, storage) and the intelligence.
The webhook returns 202 immediately and this runs afterwards, because GitHub
gives a webhook ~10 seconds before it marks the delivery failed and retries —
agent work takes longer than that.

Pipeline: fetch changed files -> extract drift facts -> persist -> agent graph
(search docs -> judge -> fix -> verify -> open the docs PR). Each capability is
gated by its credential: no GITHUB_TOKEN = archive only; no ANTHROPIC_API_KEY =
facts only. The pipeline is always demoable at whatever depth the environment
allows, and every stage failure lands in the delivery's status for replay.
"""

import json
import logging

import anthropic
import httpx

from driftguard.agent.graph import run_agent
from driftguard.agent.model import AgentError, AnthropicModel
from driftguard.analysis.pipeline import analyze_pr
from driftguard.config import Settings
from driftguard.github.client import GitHubClient, GitHubError
from driftguard.indexing.embedder import EmbeddingError, OllamaEmbedder
from driftguard.storage import db

log = logging.getLogger("driftguard.agent")


def _build_model(settings: Settings) -> AnthropicModel:
    """Seam for tests: monkeypatch this to inject a fake DriftModel."""
    return AnthropicModel(settings.anthropic_api_key, settings.judge_model)


def _build_embedder(settings: Settings) -> OllamaEmbedder:
    return OllamaEmbedder(settings.ollama_base_url, settings.embedding_model)


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
            db.save_facts(settings.database_path, delivery_id, facts)
            log.info(
                "delivery %s: %d drift fact(s): %s",
                delivery_id,
                len(facts),
                [f"{fact.kind}:{fact.symbol}" for fact in facts],
            )
            if not facts:
                db.set_status(settings.database_path, delivery_id, "no_drift")
                return
            db.set_status(settings.database_path, delivery_id, "facts_extracted")

            if not settings.anthropic_api_key:
                log.info("ANTHROPIC_API_KEY not set — facts extracted, agent skipped")
                return

            result = run_agent(
                settings,
                client,
                _build_model(settings),
                _build_embedder(settings),
                repo=row["repo"],
                pr_number=row["pr_number"],
                head_ref=head_ref,
                facts=facts,
            )
    except (GitHubError, httpx.TransportError) as exc:
        # Fail loudly into the delivery's status: the replay CLI can re-run it later.
        log.error("delivery %s: GitHub analysis failed: %s", delivery_id, exc)
        db.set_status(settings.database_path, delivery_id, "error")
        return
    except (AgentError, EmbeddingError, anthropic.APIError) as exc:
        # Facts are already saved; only the agent stage failed. Replayable too.
        log.error("delivery %s: agent failed: %s", delivery_id, exc)
        db.set_status(settings.database_path, delivery_id, "agent_error")
        return

    db.save_proposals(settings.database_path, delivery_id, result.proposals)
    if result.pr_url:
        db.set_docs_pr_url(settings.database_path, delivery_id, result.pr_url)
        db.set_status(settings.database_path, delivery_id, "docs_pr_opened")
        log.info("delivery %s: docs PR opened: %s", delivery_id, result.pr_url)
    else:
        db.set_status(settings.database_path, delivery_id, result.status)
        log.info("delivery %s: agent finished: %s", delivery_id, result.status)
