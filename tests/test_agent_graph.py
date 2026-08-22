"""Agent graph tests: search -> judge -> fix -> verify -> publish, fully offline.

FakeDriftModel (conftest) plays the LLM, HashEmbedder plays Ollama, and an
httpx.MockTransport plays GitHub — every request the publisher makes is
captured and asserted on. This is the same seam layout production uses; only
the three expensive edges are swapped.
"""

import base64
import json

import httpx

from conftest import FakeDriftModel, make_settings
from driftguard.agent.graph import run_agent
from driftguard.analysis.extractor import DriftFact
from driftguard.github.client import GitHubClient
from driftguard.indexing.embedder import HashEmbedder

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

RELEASE_NOTES_MD = """# Release notes v1

In v1 the `timeout` parameter was introduced with a default of 10.
"""

RENAME = DriftFact("renamed", "connect.timeout", "timeout", "request_timeout", "src/client.py")
DEFAULT = DriftFact("default_changed", "connect.timeout", "10", "30", "src/client.py")
REMOVED = DriftFact("removed", "close", "close", None, "src/client.py")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def make_fake_github(captured: list) -> httpx.MockTransport:
    docs = {
        "docs/config.md": ("doc-sha-1", CONFIG_MD),
        "docs/release-notes-v1.md": ("doc-sha-2", RELEASE_NOTES_MD),
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
        if path == f"/repos/{REPO}/pulls" and request.method == "POST":
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/99"})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(fake)


def run(tmp_path, facts, **setting_overrides):
    captured: list = []
    settings = make_settings(tmp_path, **setting_overrides)
    with GitHubClient("fake-token", transport=make_fake_github(captured)) as client:
        result = run_agent(
            settings,
            client,
            FakeDriftModel(),
            HashEmbedder(),
            repo=REPO,
            pr_number=7,
            head_ref=HEAD_SHA,
            facts=facts,
        )
    return result, captured


def test_rename_and_default_produce_one_verified_fix_and_a_pr(tmp_path):
    result, captured = run(tmp_path, [RENAME, DEFAULT])

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
    result, captured = run(tmp_path, [RENAME, DEFAULT])
    touched = [p for m, p, _ in captured if m == "PUT"]
    assert all("release-notes" not in path for path in touched)
    assert all("release-notes" not in p.chunk.doc_path for p in result.proposals)


def test_unfixable_finding_is_rejected_after_bounded_retries_no_pr(tmp_path):
    # FakeDriftModel cannot fix 'removed' facts (returns the text unchanged),
    # so verification fails every attempt and the finding must be dropped.
    result, captured = run(tmp_path, [REMOVED])

    assert result.status == "all_rejected"
    assert result.pr_url is None
    rejected = [p for p in result.proposals if p.status == "rejected"]
    assert rejected and all(p.attempts == 2 for p in rejected)
    assert all(p.feedback for p in rejected)  # the reason is kept as evidence
    assert not captured  # no branch, no commit, no PR


def test_open_docs_prs_off_stores_proposals_without_publishing(tmp_path):
    result, captured = run(tmp_path, [RENAME], open_docs_prs=False)

    assert result.status == "proposals_ready"
    assert result.pr_url is None
    assert any(p.status == "verified" for p in result.proposals)
    assert not [c for c in captured if c[0] in ("POST", "PUT")]
