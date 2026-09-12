"""Agent graph tests: search -> judge -> fix -> verify -> publish, fully offline.

FakeDriftModel (conftest) plays the LLM, HashEmbedder plays Ollama, and an
httpx.MockTransport plays GitHub — every request the publisher makes is
captured and asserted on. This is the same seam layout production uses; only
the three expensive edges are swapped.
"""

import base64
import json

import httpx

from conftest import UNSURE_MARKER, FakeDriftModel, make_settings
from driftguard.agent.asking import QUESTION_MARKER
from driftguard.agent.graph import run_agent
from driftguard.analysis.extractor import DriftFact
from driftguard.github.client import GitHubClient
from driftguard.indexing.embedder import HashEmbedder
from driftguard.storage import db

REPO = "abdesslem/driftguard-demo"
HEAD_SHA = "merge-sha"

CONFIG_MD = """# Configuration

## Timeouts

Set `timeout` to control how long connect() waits before giving up.
The default `timeout` is 10 seconds.

## Retries

The client retries failed requests twice.

## Cleanup

Always call close(session) when you are done with a connection.
"""

# Only injected by the ask-flow tests: the marker makes the fake judge unsure.
LIMITS_MD = f"""# Limits

## General limits

Every operation gives up after a timeout in the default setup. {UNSURE_MARKER}
"""

RELEASE_NOTES_MD = """# Release notes v1

In v1 the `timeout` parameter was introduced with a default of 10.
"""

RENAME = DriftFact("renamed", "connect.timeout", "timeout", "request_timeout", "src/client.py")
DEFAULT = DriftFact("default_changed", "connect.timeout", "10", "30", "src/client.py")
REMOVED = DriftFact("removed", "close", "close", None, "src/client.py")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def make_fake_github(captured: list, extra_docs: dict | None = None) -> httpx.MockTransport:
    docs = {
        "docs/config.md": ("doc-sha-1", CONFIG_MD),
        "docs/release-notes-v1.md": ("doc-sha-2", RELEASE_NOTES_MD),
        **(extra_docs or {}),
    }

    def fake(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method in ("POST", "PUT"):
            captured.append((request.method, path, json.loads(request.content)))

        if path == f"/repos/{REPO}/git/trees/{HEAD_SHA}":
            tree = [{"path": p, "type": "blob"} for p in docs]
            tree.append({"path": "src/client.py", "type": "blob"})
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        for doc_path, (sha, text) in docs.items():
            if path == f"/repos/{REPO}/contents/{doc_path}":
                if request.method == "PUT":
                    return httpx.Response(200, json={"content": {"sha": "new-sha"}})
                return httpx.Response(
                    200, json={"encoding": "base64", "content": _b64(text), "sha": sha}
                )
        if path == f"/repos/{REPO}/git/refs" and request.method == "POST":
            return httpx.Response(201, json={"ref": "created"})
        if path == f"/repos/{REPO}/issues/7/comments" and request.method == "POST":
            return httpx.Response(201, json={"id": 4242})
        if path == f"/repos/{REPO}/pulls" and request.method == "POST":
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/99"})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(fake)


def run(tmp_path, facts, extra_docs=None, **setting_overrides):
    captured: list = []
    settings = make_settings(tmp_path, **setting_overrides)
    with GitHubClient("fake-token", transport=make_fake_github(captured, extra_docs)) as client:
        result = run_agent(
            settings,
            client,
            FakeDriftModel(),
            HashEmbedder(),
            repo=REPO,
            pr_number=7,
            head_ref=HEAD_SHA,
            facts=facts,
            delivery_id="delivery-1",
        )
    return result, settings, captured


def test_rename_and_default_produce_one_verified_fix_and_a_pr(tmp_path):
    result, _, captured = run(tmp_path, [RENAME, DEFAULT])

    assert result.status == "published"
    assert result.pr_url == f"https://github.com/{REPO}/pull/99"

    # Both facts hit the same section -> ONE proposal, carrying both.
    published = [p for p in result.proposals if p.status == "published"]
    assert len(published) == 1
    assert sorted(f.kind for f in published[0].facts) == ["default_changed", "renamed"]

    # The branch, the file update, and the PR — in that order.
    methods = [(m, p) for m, p, _ in captured]
    assert methods[0] == ("POST", f"/repos/{REPO}/git/refs")
    assert ("PUT", f"/repos/{REPO}/contents/docs/config.md") in methods
    assert methods[-1] == ("POST", f"/repos/{REPO}/pulls")

    branch_body = captured[0][2]
    assert branch_body == {"ref": "refs/heads/driftguard/docs-pr-7", "sha": HEAD_SHA}

    put_body = next(body for m, p, body in captured if m == "PUT")
    new_doc = base64.b64decode(put_body["content"]).decode()
    assert "request_timeout" in new_doc
    assert "`timeout`" not in new_doc  # the old name is gone
    assert "30 seconds" in new_doc  # the new default is in
    assert "## Retries" in new_doc  # untouched section survived intact
    assert put_body["sha"] == "doc-sha-1"  # concurrency guard sent

    pr_body = captured[-1][2]
    assert pr_body["base"] == "main"
    assert "docs/config.md" in pr_body["body"]


def test_historical_pages_are_never_edited(tmp_path):
    result, _, captured = run(tmp_path, [RENAME, DEFAULT])
    touched = [p for m, p, _ in captured if m == "PUT"]
    assert all("release-notes" not in path for path in touched)
    assert all("release-notes" not in p.chunk.doc_path for p in result.proposals)


def test_unfixable_finding_is_rejected_after_bounded_retries_no_pr(tmp_path):
    # FakeDriftModel cannot fix 'removed' facts (returns the text unchanged),
    # so verification fails every attempt and the finding must be dropped.
    result, _, captured = run(tmp_path, [REMOVED])

    assert result.status == "all_rejected"
    assert result.pr_url is None
    rejected = [p for p in result.proposals if p.status == "rejected"]
    assert rejected and all(p.attempts == 2 for p in rejected)
    assert all(p.feedback for p in rejected)  # the reason is kept as evidence
    assert not captured  # no branch, no commit, no PR


def test_open_docs_prs_off_stores_proposals_without_publishing(tmp_path):
    result, _, captured = run(tmp_path, [RENAME], open_docs_prs=False)

    assert result.status == "proposals_ready"
    assert result.pr_url is None
    assert any(p.status == "verified" for p in result.proposals)
    assert not [c for c in captured if c[0] in ("POST", "PUT")]


AMBIGUOUS_DOCS = {"docs/limits.md": ("doc-sha-3", LIMITS_MD)}


def test_unsure_verdict_posts_a_question_and_clear_fixes_still_publish(tmp_path):
    result, settings, captured = run(tmp_path, [RENAME, DEFAULT], extra_docs=AMBIGUOUS_DOCS)

    # Partial publishing: the open question did NOT block the verified fix.
    assert result.status == "published"
    assert [q.label for q in result.questions] == ["Q1"]

    # The digest went to the merged PR, carries the label, the question and
    # our marker (so the webhook can ignore our own comment later).
    comments = [body for m, p, body in captured if p.endswith("/issues/7/comments")]
    assert len(comments) == 1
    digest = comments[0]["body"]
    assert "Q1" in digest and "docs/limits.md" in digest
    assert QUESTION_MARKER in digest

    # Persisted first, comment id recorded after: the answer webhook can match it.
    rows = db.questions_for_delivery(settings.database_path, "delivery-1")
    assert [(r["q_label"], r["status"], r["comment_id"]) for r in rows] == [("Q1", "open", 4242)]
    assert db.has_open_questions(settings.database_path, REPO, 7)


def test_ask_developer_off_stores_questions_without_commenting(tmp_path):
    result, settings, captured = run(
        tmp_path, [DEFAULT], extra_docs=AMBIGUOUS_DOCS, ask_developer=False, open_docs_prs=False
    )

    assert [q.label for q in result.questions] == ["Q1"]
    assert not [c for c in captured if c[1].endswith("/comments")]
    rows = db.questions_for_delivery(settings.database_path, "delivery-1")
    assert [(r["q_label"], r["comment_id"]) for r in rows] == [("Q1", None)]


def test_questions_capped_per_run(tmp_path):
    many = {
        f"docs/limits{i}.md": (f"sha-{i}", LIMITS_MD.replace("# Limits", f"# Limits {i}"))
        for i in range(5)
    }
    result, _, captured = run(
        tmp_path, [DEFAULT], extra_docs=many, max_questions_per_run=2, open_docs_prs=False
    )

    assert len(result.questions) == 2
    comments = [body for m, p, body in captured if p.endswith("/issues/7/comments")]
    assert len(comments) == 1
    assert "Q3" not in comments[0]["body"]
