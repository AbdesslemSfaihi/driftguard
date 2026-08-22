"""Pipeline tests against a fake GitHub (httpx.MockTransport — zero network)."""

import base64
import json

import httpx

from driftguard.analysis.pipeline import analyze_pr
from driftguard.github.client import GitHubClient

REPO = "abdesslem/driftguard-demo"
BASE_SHA = "base-sha"
HEAD_SHA = "head-sha"

OLD_CLIENT_PY = "def connect(host, timeout=10):\n    ...\n"
NEW_CLIENT_PY = "def connect(host, request_timeout=30):\n    ...\n"


def _b64(source: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(source.encode()).decode()}


def fake_github(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    ref = request.url.params.get("ref")

    if path == f"/repos/{REPO}/pulls/7/files":
        return httpx.Response(
            200,
            json=[
                {"filename": "demo/client.py", "status": "modified"},
                {"filename": "README.md", "status": "modified"},  # not .py -> skipped
                {"filename": "tests/test_client.py", "status": "modified"},  # test -> skipped
                {"filename": "demo/new_feature.py", "status": "added"},  # added -> skipped
            ],
        )
    if path == f"/repos/{REPO}/contents/demo/client.py":
        return httpx.Response(200, json=_b64(OLD_CLIENT_PY if ref == BASE_SHA else NEW_CLIENT_PY))
    return httpx.Response(404, json={"message": "Not Found"})


def make_client() -> GitHubClient:
    return GitHubClient("fake-token", transport=httpx.MockTransport(fake_github))


def test_analyze_pr_extracts_facts_from_relevant_files_only():
    with make_client() as client:
        facts = analyze_pr(client, repo=REPO, number=7, base_ref=BASE_SHA, head_ref=HEAD_SHA)

    assert sorted(fact.kind for fact in facts) == ["default_changed", "renamed"]
    assert all(fact.file == "demo/client.py" for fact in facts)


def test_removed_file_reports_all_its_functions_as_removed():
    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}/pulls/8/files":
            return httpx.Response(
                200, json=[{"filename": "demo/legacy.py", "status": "removed"}]
            )
        if request.url.path == f"/repos/{REPO}/contents/demo/legacy.py":
            return httpx.Response(200, json=_b64("def legacy_mode():\n    ...\n"))
        return httpx.Response(404, json={"message": "Not Found"})

    with GitHubClient("fake-token", transport=httpx.MockTransport(fake)) as client:
        facts = analyze_pr(client, repo=REPO, number=8, base_ref=BASE_SHA, head_ref=HEAD_SHA)

    assert [(fact.kind, fact.symbol) for fact in facts] == [("removed", "legacy_mode")]


def test_file_content_returns_none_on_404():
    with make_client() as client:
        assert client.file_content(REPO, "does/not/exist.py", BASE_SHA) is None


def test_end_to_end_webhook_to_facts(tmp_path, monkeypatch):
    """Full slice: signed webhook in -> facts in the database.

    TestClient runs FastAPI background tasks synchronously after the response,
    so the whole pipeline executes inside this test. GitHubClient is patched to
    use the fake transport; everything else is the real code path.
    """
    from fastapi.testclient import TestClient

    import driftguard.agent.handler as handler
    from conftest import merged_pr_payload, post_webhook
    from driftguard.api.webhook import create_app
    from driftguard.config import Settings
    from driftguard.storage import db

    settings = Settings(
        github_webhook_secret="test-secret",
        github_token="fake-token",
        database_path=tmp_path / "e2e.db",
    )
    real_client = GitHubClient
    monkeypatch.setattr(
        handler,
        "GitHubClient",
        lambda token: real_client(token, transport=httpx.MockTransport(fake_github)),
    )

    payload = merged_pr_payload(number=7)
    payload["pull_request"]["base"] = {"sha": BASE_SHA}
    payload["pull_request"]["merge_commit_sha"] = HEAD_SHA
    payload["repository"]["full_name"] = REPO

    client = TestClient(create_app(settings))
    response = post_webhook(client, payload, delivery_id="e2e-1")
    assert response.json()["status"] == "accepted"

    row = db.get_delivery(settings.database_path, "e2e-1")
    assert row["status"] == "facts_extracted"
    facts = db.facts_for_delivery(settings.database_path, "e2e-1")
    assert sorted(fact["kind"] for fact in facts) == ["default_changed", "renamed"]
    stored = json.loads(row["payload_json"])
    assert stored["pull_request"]["merge_commit_sha"] == HEAD_SHA
