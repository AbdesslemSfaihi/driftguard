"""Answer processing: a maintainer's reply resolves an open question.

The seam layout mirrors test_agent_graph: FakeDriftModel reads the answer
(keyword-based), httpx.MockTransport plays GitHub, and the questions table is
seeded directly — the ask side already has its own tests. What is asserted
here is the authority chain: the human's decision is applied without a judge
call, the fixer's output still faces deterministic verification, and anything
unclear or unmatched changes NOTHING.
"""

import base64
import json

import httpx

from conftest import UNSURE_MARKER, FakeDriftModel, make_settings
from driftguard.agent.answers import process_answers
from driftguard.github.client import GitHubClient
from driftguard.storage import db

REPO = "abdesslem/driftguard-demo"
PR_NUMBER = 7

LIMITS_MD = f"""# Limits

## General limits

Every operation gives up after 10 seconds in the default setup. {UNSURE_MARKER}
"""

FACT_JSON = json.dumps(
    {
        "kind": "default_changed",
        "symbol": "connect.timeout",
        "old": "10",
        "new": "30",
        "file": "src/client.py",
    }
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def make_fake_github(captured: list, *, doc_exists: bool = True) -> httpx.MockTransport:
    def fake(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method in ("POST", "PUT"):
            captured.append((request.method, path, json.loads(request.content)))

        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == f"/repos/{REPO}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "main-sha"}})
        if path == f"/repos/{REPO}/contents/docs/limits.md":
            if not doc_exists:
                return httpx.Response(404, json={"message": "Not Found"})
            if request.method == "PUT":
                return httpx.Response(200, json={"content": {"sha": "new-sha"}})
            return httpx.Response(
                200, json={"encoding": "base64", "content": _b64(LIMITS_MD), "sha": "doc-sha"}
            )
        if path == f"/repos/{REPO}/git/refs" and request.method == "POST":
            return httpx.Response(201, json={"ref": "created"})
        if path == f"/repos/{REPO}/pulls" and request.method == "POST":
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/100"})
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(fake)


def seed_question(settings) -> None:
    db.save_questions(
        settings.database_path,
        "delivery-1",
        repo=REPO,
        pr_number=PR_NUMBER,
        questions=[
            ("Q1", FACT_JSON, "docs/limits.md", "Limits > General limits", "Is this connect()?")
        ],
    )


def answer(tmp_path, body, *, doc_exists=True, **setting_overrides):
    captured: list = []
    settings = make_settings(tmp_path, **setting_overrides)
    seed_question(settings)
    transport = make_fake_github(captured, doc_exists=doc_exists)
    with GitHubClient("fake-token", transport=transport) as client:
        result = process_answers(
            settings,
            client,
            FakeDriftModel(),
            delivery_id="answer-1",
            repo=REPO,
            pr_number=PR_NUMBER,
            comment_body=body,
            author="maria",
        )
    return result, settings, captured


def question_row(settings):
    rows = db.questions_for_delivery(settings.database_path, "delivery-1")
    assert len(rows) == 1
    return rows[0]


def test_confirming_answer_publishes_a_followup_pr(tmp_path):
    result, settings, captured = answer(tmp_path, "Q1: yes, that is connect()'s timeout")

    assert result.status == "answers_processed"
    assert result.pr_url == f"https://github.com/{REPO}/pull/100"

    # The fix was applied to TODAY's doc, on a dedicated answers branch.
    branch_body = next(body for m, p, body in captured if p.endswith("/git/refs"))
    assert branch_body == {
        "ref": "refs/heads/driftguard/docs-pr-7-answers-answer-1",
        "sha": "main-sha",
    }
    put_body = next(body for m, p, body in captured if m == "PUT")
    new_doc = base64.b64decode(put_body["content"]).decode()
    assert "30 seconds" in new_doc and "10 seconds" not in new_doc
    assert put_body["sha"] == "doc-sha"

    # The PR cites the human's answer as the evidence.
    pr_body = next(body for m, p, body in captured if p.endswith("/pulls"))
    assert "maintainer @maria confirmed" in pr_body["body"]

    row = question_row(settings)
    assert row["status"] == "resolved_fixed"
    assert row["answered_by"] == "maria"
    assert not db.has_open_questions(settings.database_path, REPO, PR_NUMBER)


def test_negative_answer_resolves_without_touching_anything(tmp_path):
    result, settings, captured = answer(tmp_path, "Q1: no — that section is about the CLI flag")

    assert result.status == "answers_processed"
    assert result.pr_url is None
    assert not captured  # no branch, no commit, no PR
    assert question_row(settings)["status"] == "resolved_accurate"


def test_unclear_reply_leaves_the_question_open(tmp_path):
    result, settings, captured = answer(tmp_path, "Q1: hmm, let me check with the team")

    assert result.status == "answers_processed"
    assert not captured
    assert question_row(settings)["status"] == "open"
    assert db.has_open_questions(settings.database_path, REPO, PR_NUMBER)


def test_comment_without_labels_matches_nothing(tmp_path):
    result, settings, captured = answer(tmp_path, "great bot, thanks!")

    assert result.status == "no_answers_matched"
    assert not captured
    assert question_row(settings)["status"] == "open"


def test_vanished_section_goes_stale_instead_of_guessing(tmp_path):
    result, settings, captured = answer(tmp_path, "Q1: yes it is", doc_exists=False)

    assert result.status == "answers_processed"
    assert result.pr_url is None
    assert not captured
    assert question_row(settings)["status"] == "stale"
