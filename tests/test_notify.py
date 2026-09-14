"""Notifications: one event per finished run, built from the story, sent
fire-and-forget. What is asserted: the event tells the whole outcome on its
own, the severity is a three-way routing key, failures never raise, and the
handler emits exactly one notification per processed delivery."""

import json

import httpx
import pytest

from conftest import issue_comment_payload, make_settings, merged_pr_payload
from driftguard.agent.graph import Proposal
from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import DocChunk
from driftguard.notify import TOKEN_HEADER, build_event, notify, send, severity_for
from driftguard.storage import db

REPO = "abdesslem/driftguard-demo"
FACT = DriftFact("default_changed", "deliver.attempts", "3", "5", "src/webhooks.py")
CHUNK = DocChunk(
    "docs/webhooks.md", "Webhooks > Delivery retries", "attempts=3", "docs/webhooks.md::1", 4, 8
)


def seed_merged_pr(settings, delivery_id="d-1", status="docs_pr_opened"):
    db.record_delivery(
        settings.database_path,
        delivery_id=delivery_id,
        event="pull_request",
        action="closed",
        repo=REPO,
        pr_number=7,
        payload=merged_pr_payload(7),
    )
    db.save_facts(settings.database_path, delivery_id, [FACT])
    proposal = Proposal(chunk=CHUNK, facts=[FACT], reasons=["shows attempts=3"], new_text="=5")
    proposal.status = "published"
    proposal.attempts = 1
    db.save_proposals(settings.database_path, delivery_id, [proposal])
    db.save_questions(
        settings.database_path,
        delivery_id,
        repo=REPO,
        pr_number=7,
        questions=[("Q1", json.dumps(vars(FACT)), "docs/faq.md", "FAQ > Retries", "deliver()?")],
    )
    db.set_docs_pr_url(settings.database_path, delivery_id, f"https://github.com/{REPO}/pull/8")
    db.set_status(settings.database_path, delivery_id, status)


def test_merged_pr_event_tells_the_whole_outcome(tmp_path):
    settings = make_settings(tmp_path, public_base_url="https://drift.example.com/")
    seed_merged_pr(settings)

    event = build_event(settings, "d-1")

    assert event["kind"] == "merged_pr" and event["status"] == "docs_pr_opened"
    assert event["severity"] == "action"
    assert event["repo"] == REPO and event["pr_number"] == 7
    assert event["pr_title"] == "feat: rename timeout to request_timeout"
    assert event["pr_url"] == f"https://github.com/{REPO}/pull/7"
    assert event["docs_pr_url"].endswith("/pull/8") and event["docs_pr_number"] == 8
    assert event["story_url"] == "https://drift.example.com/ui/deliveries/d-1"
    assert [f["symbol"] for f in event["facts"]] == ["deliver.attempts"]
    assert event["proposals"] == {"published": 1, "verified": 0, "rejected": 0, "stale": 0}
    assert event["open_questions"] == 1 and event["questions"][0]["label"] == "Q1"
    assert "docs PR #8 opened" in event["summary"]
    assert "1 question(s) waiting for a maintainer (Q1)" in event["summary"]


def test_answer_event_reports_on_the_merge_it_continues(tmp_path):
    settings = make_settings(tmp_path)
    seed_merged_pr(settings)
    db.record_delivery(
        settings.database_path,
        delivery_id="a-1",
        event="issue_comment",
        action="created",
        repo=REPO,
        pr_number=7,
        payload=issue_comment_payload(7, body="Q1: hmm", author="maria"),
    )
    db.set_status(settings.database_path, "a-1", "answers_processed")

    event = build_event(settings, "a-1")

    assert event["kind"] == "answer" and event["author"] == "maria"
    assert event["severity"] == "info"
    assert event["story_url"].endswith("/ui/deliveries/d-1")  # the merge's page, not the comment's
    assert event["questions"][0]["label"] == "Q1"
    assert "did not settle" in event["summary"]


def test_no_drift_is_plain_info(tmp_path):
    settings = make_settings(tmp_path)
    db.record_delivery(
        settings.database_path,
        delivery_id="d-2",
        event="pull_request",
        action="closed",
        repo=REPO,
        pr_number=9,
        payload=merged_pr_payload(9),
    )
    db.set_status(settings.database_path, "d-2", "no_drift")
    event = build_event(settings, "d-2")
    assert event["severity"] == "info"
    assert event["summary"] == f"PR #9 on {REPO}: no documentation drift"


@pytest.mark.parametrize(
    ("status", "severity"),
    [
        ("docs_pr_opened", "action"),
        ("questions_open", "action"),
        ("proposals_ready", "action"),
        ("error", "alert"),
        ("agent_error", "alert"),
        ("all_rejected", "alert"),
        ("no_drift", "info"),
        ("logged", "info"),
        ("answers_processed", "info"),
    ],
)
def test_severity_is_a_three_way_routing_key(status, severity):
    assert severity_for(status) == severity


def test_send_posts_json_with_the_shared_token(tmp_path):
    captured: list[httpx.Request] = []

    def fake(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    settings = make_settings(
        tmp_path,
        notify_webhook_url="http://n8n.local/webhook/driftguard",
        notify_webhook_token="s3cret",
    )
    event = {"delivery_id": "d-1", "status": "no_drift", "severity": "info"}
    assert send(settings, event, transport=httpx.MockTransport(fake)) is True

    [request] = captured
    assert str(request.url) == "http://n8n.local/webhook/driftguard"
    assert request.headers[TOKEN_HEADER] == "s3cret"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content)["status"] == "no_drift"


def test_failures_are_logged_never_raised(tmp_path):
    settings = make_settings(tmp_path, notify_webhook_url="http://n8n.local/webhook/driftguard")
    rejected = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    assert send(settings, {"delivery_id": "d-1"}, transport=rejected) is False

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    assert send(settings, {"delivery_id": "d-1"}, transport=httpx.MockTransport(down)) is False


def test_notify_is_a_no_op_without_a_url(tmp_path):
    settings = make_settings(tmp_path)  # notify_webhook_url pinned to ""
    seed_merged_pr(settings)

    def must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no URL configured, nothing may be sent")

    assert notify(settings, "d-1", transport=httpx.MockTransport(must_not_be_called)) is False


def test_every_processed_delivery_ends_with_one_notification(tmp_path, monkeypatch):
    from driftguard.agent import handler

    sent: list[str] = []
    monkeypatch.setattr(handler, "notify", lambda settings, delivery_id: sent.append(delivery_id))
    settings = make_settings(tmp_path)
    db.record_delivery(
        settings.database_path,
        delivery_id="lost-1",
        event="pull_request",
        action="closed",
        repo=REPO,
        pr_number=1,
        payload=merged_pr_payload(),
    )

    handler.process_delivery(settings, "lost-1")

    assert sent == ["lost-1"]
    assert db.get_delivery(settings.database_path, "lost-1")["status"] == "logged"
