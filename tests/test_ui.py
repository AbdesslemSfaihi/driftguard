"""The story page's JSON: everything the agent stored about one merged PR,
in the order it happened, plus the follow-up answer deliveries."""

import json

from fastapi.testclient import TestClient

from conftest import issue_comment_payload, make_settings, merged_pr_payload
from driftguard.agent.graph import Proposal, SectionJudgment
from driftguard.analysis.extractor import DriftFact
from driftguard.api.ui import diff_lines
from driftguard.api.webhook import create_app
from driftguard.indexing.chunker import DocChunk
from driftguard.storage import db

REPO = "abdesslem/driftguard-demo"
MERGE_SHA = "m" * 40
BASE_SHA = "b" * 40
FACT = DriftFact("default_changed", "deliver.attempts", "3", "5", "src/webhooks.py")
PAGE = DocChunk("docs/webhooks.md", "Webhooks > Delivery retries", "attempts=3", "w::1", 4, 6)
FAQ = DocChunk("docs/faq.md", "FAQ > How many retries?", "3 attempts", "f::2", 8, 9)
NOTES = DocChunk("docs/release-notes-v1.md", "v1", "attempts=3 arrived", "n::0", 0, 1)


def seed(settings) -> None:
    path = settings.database_path
    payload = merged_pr_payload(number=7)
    payload["pull_request"].update(
        {
            "html_url": f"https://github.com/{REPO}/pull/7",
            "merge_commit_sha": MERGE_SHA,
            "base": {"sha": BASE_SHA},
            "merged_at": "2026-09-12T16:20:00Z",
        }
    )
    db.record_delivery(
        path,
        delivery_id="merge-1",
        event="pull_request",
        action="closed",
        repo=REPO,
        pr_number=7,
        payload=payload,
    )
    db.save_facts(path, "merge-1", [FACT])
    db.save_verdicts(
        path,
        "merge-1",
        [
            SectionJudgment(FACT, PAGE, 1, 1, "drifted", "shows attempts=3 as current"),
            SectionJudgment(FACT, FAQ, 3, 2, "unsure", "names no function", "About deliver()?"),
            SectionJudgment(FACT, NOTES, 4, None, "historical", "release notes"),
        ],
    )
    fixed = Proposal(chunk=PAGE, facts=[FACT], reasons=["shows attempts=3"], new_text="attempts=5")
    fixed.status, fixed.attempts = "published", 1
    db.save_proposals(path, "merge-1", [fixed])
    db.save_questions(
        path,
        "merge-1",
        repo=REPO,
        pr_number=7,
        questions=[
            ("Q1", json.dumps(vars(FACT)), FAQ.doc_path, FAQ.heading_path, "About deliver()?")
        ],
    )
    db.set_questions_comment(path, "merge-1", 4242)
    db.set_docs_pr_url(path, "merge-1", f"https://github.com/{REPO}/pull/8")
    db.set_status(path, "merge-1", "docs_pr_opened")

    db.record_delivery(
        path,
        delivery_id="answer-1",
        event="issue_comment",
        action="created",
        repo=REPO,
        pr_number=7,
        payload=issue_comment_payload(number=7, body="Q1: yes"),
    )
    question_id = db.questions_for_delivery(path, "merge-1")[0]["id"]
    db.resolve_question(
        path, question_id, status="resolved_fixed", answer="yes", answered_by="maria"
    )
    db.set_docs_pr_url(path, "answer-1", f"https://github.com/{REPO}/pull/9")
    db.set_status(path, "answer-1", "docs_pr_opened")


def make_client(tmp_path) -> TestClient:
    settings = make_settings(tmp_path)
    seed(settings)
    return TestClient(create_app(settings))


def test_story_tells_every_stage_in_order(tmp_path):
    story = make_client(tmp_path).get("/ui/api/deliveries/merge-1").json()

    expected_stage = {"index": 5, "state": "done", "status": "docs_pr_opened", "final": True}
    assert story["stage"] == expected_stage
    assert story["pull_request"]["author"] == "abdesslem"
    assert story["pull_request"]["compare_url"].endswith(f"{BASE_SHA}...{MERGE_SHA}")
    assert [f["symbol"] for f in story["facts"]] == ["deliver.attempts"]
    assert story["facts"][0]["file_url"] == f"https://github.com/{REPO}/blob/{MERGE_SHA}/src/webhooks.py"
    assert [v["verdict"] for v in story["verdicts"]] == ["drifted", "unsure", "historical"]
    assert story["verdicts"][1]["question"] == "About deliver()?"
    proposal = story["proposals"][0]
    assert (proposal["status"], proposal["attempts"]) == ("published", 1)
    assert [line["type"] for line in proposal["diff"]] == ["del", "add"]
    question = story["questions"][0]
    assert (question["status"], question["answer"], question["answered_by"]) == (
        "resolved_fixed",
        "yes",
        "maria",
    )
    assert question["comment_url"].endswith("#issuecomment-4242")
    assert [f["docs_pr_url"] for f in story["followups"]] == [f"https://github.com/{REPO}/pull/9"]
    assert story["followups"][0]["comment"]["author"] == "maria"


def test_answer_deliveries_point_at_their_pull_requests_story(tmp_path):
    items = make_client(tmp_path).get("/ui/api/deliveries").json()
    by_id = {item["delivery_id"]: item for item in items}
    assert by_id["answer-1"]["story_id"] == "merge-1"
    assert by_id["merge-1"]["story_id"] == "merge-1"
    assert by_id["merge-1"]["title"] == "feat: rename timeout to request_timeout"
    assert items[0]["delivery_id"] == "answer-1"  # newest first


def test_pages_and_static_assets_are_served(tmp_path):
    client = make_client(tmp_path)
    assert "text/html" in client.get("/ui").headers["content-type"]
    assert "text/html" in client.get("/ui/deliveries/merge-1").headers["content-type"]
    assert client.get("/ui/static/ui.js").status_code == 200
    assert client.get("/ui/static/ui.css").status_code == 200
    assert client.get("/ui/api/deliveries/nope").status_code == 404


def test_diff_lines_marks_changed_lines_only():
    diff = diff_lines("a\nb\nc\n", "a\nB\nc\n")
    assert [(line["type"], line["text"]) for line in diff] == [
        ("same", "a"),
        ("del", "b"),
        ("add", "B"),
        ("same", "c"),
    ]
