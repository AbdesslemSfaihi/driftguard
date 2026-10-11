"""The presentation demo is rehearsable offline.

Every scenario in the demo CLI must apply to the baseline exactly as declared,
leave valid Python behind, and yield exactly the drift facts the narrative
promises. The baseline docs must contain the statements those facts contradict
and nothing more: one handbook section that names the rule (fixed
automatically), one FAQ answer that does not (a question, never a guess) and
one changelog entry (historical, never edited). The GitHub side of `merge`
and `reset` runs against a fake API, so the demo-day path is exercised too.
"""

import base64
import json
from pathlib import Path

import httpx
import pytest

from driftguard.analysis.extractor import extract_drift_facts
from driftguard.cli.demo import (
    CODE_FILE,
    SCENARIOS,
    DemoError,
    baseline_files,
    merge,
    reset,
)
from driftguard.github.client import GitHubClient
from driftguard.indexing.chunker import chunk_markdown

BASELINE_DIR = Path(__file__).resolve().parent.parent / "demo" / "tinybank"
REPO = "someone/tinybank-demo"


@pytest.fixture(scope="module")
def baseline() -> dict[str, str]:
    return baseline_files(BASELINE_DIR)


def _sections(baseline: dict[str, str], doc: str) -> dict[str, str]:
    return {chunk.heading_path: chunk.text for chunk in chunk_markdown(baseline[doc], doc)}


# ---------- the scenario table ----------


def test_every_scenario_yields_exactly_the_promised_facts(baseline):
    source = baseline[CODE_FILE]
    for scenario in SCENARIOS.values():
        merged = scenario.apply(source)
        compile(merged, CODE_FILE, "exec")  # a developer could really merge this
        facts = extract_drift_facts(source, merged, CODE_FILE)
        assert [(f.kind, f.symbol) for f in facts] == list(scenario.expected_facts), scenario.key


def test_one_scenario_per_drift_kind():
    kinds = sorted(kind for s in SCENARIOS.values() for kind, _ in s.expected_facts)
    assert kinds == sorted(
        ["constant_changed", "default_changed", "removed", "renamed", "signature_changed"]
    )


def test_a_scenario_refuses_a_repo_that_is_not_at_the_baseline(baseline):
    scenario = SCENARIOS["minimum-deposit"]
    already_merged = scenario.apply(baseline[CODE_FILE])
    with pytest.raises(DemoError, match="reset"):
        scenario.apply(already_merged)


# ---------- the baseline docs: the blast radius of the headline scenario ----------


def test_the_handbook_names_the_rule_and_states_the_old_value(baseline):
    opening = _sections(baseline, "docs/account-rules.md")["Account rules > Opening an account"]
    assert "MINIMUM_DEPOSIT" in opening
    assert "100" in opening


def test_the_faq_states_the_old_value_without_naming_the_rule(baseline):
    """Unattributed on purpose: the judge must ask, not guess. If someone 'improves'
    the FAQ by naming the constant, the ask act of the demo silently disappears."""
    heading = "Frequently asked questions > How much money do I need to open an account?"
    faq = _sections(baseline, "docs/faq.md")[heading]
    assert "100" in faq
    assert "MINIMUM_DEPOSIT" not in faq
    assert "open_account" not in faq


def test_only_three_pages_state_the_old_minimum(baseline):
    """One fix (handbook), one question (FAQ), one page left alone (changelog)."""
    pages = sorted(
        doc for doc, text in baseline.items() if doc.endswith(".md") and "100 EUR" in text
    )
    assert pages == ["docs/account-rules.md", "docs/changelog.md", "docs/faq.md"]


# ---------- merge and reset against a fake GitHub ----------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _fake_github(captured: list, files: dict[str, str], open_prs: list, branches: list):
    """Just enough of the REST API for merge() and reset(). Every write is captured."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            body = json.loads(request.content) if request.content else None
            captured.append((request.method, path, body))

        contents_prefix = f"/repos/{REPO}/contents/"
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == f"/repos/{REPO}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "main-sha"}})
        if path.startswith(contents_prefix):
            file_path = path.removeprefix(contents_prefix)
            if request.method == "PUT":
                return httpx.Response(200, json={"content": {"sha": "new-sha"}})
            if file_path in files:
                content = {"encoding": "base64", "content": _b64(files[file_path]), "sha": "blob"}
                return httpx.Response(200, json=content)
            return httpx.Response(404, json={"message": "Not Found"})
        if path == f"/repos/{REPO}/git/refs" and request.method == "POST":
            return httpx.Response(201, json={"ref": "created"})
        if path == f"/repos/{REPO}/pulls" and request.method == "POST":
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/12"})
        if path == f"/repos/{REPO}/pulls" and request.method == "GET":
            return httpx.Response(200, json=open_prs)
        if path == f"/repos/{REPO}/pulls/12/merge" and request.method == "PUT":
            return httpx.Response(200, json={"sha": "merge-sha"})
        if path.startswith(f"/repos/{REPO}/pulls/") and request.method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        if path.startswith(f"/repos/{REPO}/git/matching-refs/heads/"):
            prefix = path.removeprefix(f"/repos/{REPO}/git/matching-refs/heads/")
            refs = [{"ref": f"refs/heads/{b}"} for b in branches if b.startswith(prefix)]
            return httpx.Response(200, json=refs)
        if path.startswith(f"/repos/{REPO}/git/refs/heads/") and request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    return httpx.MockTransport(handler)


def test_merge_opens_and_merges_the_scenario_pull_request(baseline):
    captured: list = []
    transport = _fake_github(captured, baseline, open_prs=[], branches=[])
    with GitHubClient("token", transport=transport) as client:
        number, url = merge(client, REPO, SCENARIOS["minimum-deposit"])

    assert (number, url) == (12, f"https://github.com/{REPO}/pull/12")
    assert [(method, path) for method, path, _ in captured] == [
        ("POST", f"/repos/{REPO}/git/refs"),
        ("PUT", f"/repos/{REPO}/contents/{CODE_FILE}"),
        ("POST", f"/repos/{REPO}/pulls"),
        ("PUT", f"/repos/{REPO}/pulls/12/merge"),
    ]
    assert captured[0][2] == {"ref": "refs/heads/demo/minimum-deposit", "sha": "main-sha"}
    commit = captured[1][2]
    assert commit["branch"] == "demo/minimum-deposit"
    assert commit["sha"] == "blob"  # the blob sha guards against clobbering a concurrent edit
    assert "MINIMUM_DEPOSIT = 150" in base64.b64decode(commit["content"]).decode()
    assert captured[2][2]["head"] == "demo/minimum-deposit"
    assert captured[2][2]["base"] == "main"


def test_reset_restores_drifted_files_and_sweeps_both_sides_branches(baseline):
    after_rehearsal = dict(baseline)
    after_rehearsal[CODE_FILE] = SCENARIOS["minimum-deposit"].apply(baseline[CODE_FILE])
    del after_rehearsal["docs/faq.md"]  # a file missing on the remote is created, not skipped
    open_prs = [
        {"number": 13, "head": {"ref": "driftguard/docs-pr-12"}, "html_url": "u1"},
        {"number": 14, "head": {"ref": "feature/unrelated"}, "html_url": "u2"},
    ]
    branches = ["demo/minimum-deposit", "driftguard/docs-pr-12", "feature/unrelated"]
    captured: list = []
    transport = _fake_github(captured, after_rehearsal, open_prs, branches)
    with GitHubClient("token", transport=transport) as client:
        done = reset(client, REPO, BASELINE_DIR)

    assert done == [
        "created docs/faq.md",
        f"restored {CODE_FILE}",
        "closed PR #13 (driftguard/docs-pr-12)",
        "deleted branch demo/minimum-deposit",
        "deleted branch driftguard/docs-pr-12",
    ]
    restored = next(body for m, p, body in captured if m == "PUT" and p.endswith(CODE_FILE))
    assert "MINIMUM_DEPOSIT = 100" in base64.b64decode(restored["content"]).decode()
    assert restored["sha"] == "blob"
    created = next(body for m, p, body in captured if m == "PUT" and p.endswith("faq.md"))
    assert "sha" not in created
    assert ("PATCH", f"/repos/{REPO}/pulls/14", {"state": "closed"}) not in captured
    assert ("DELETE", f"/repos/{REPO}/git/refs/heads/feature/unrelated", None) not in captured
