import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from driftguard.agent.model import SectionVerdict
from driftguard.api.webhook import create_app
from driftguard.config import Settings

TEST_SECRET = "test-secret"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Hermetic Settings: credentials empty unless a test opts in, so a
    developer's local .env can never change what the suite exercises."""
    values = {
        "github_webhook_secret": TEST_SECRET,
        "github_token": "",
        "anthropic_api_key": "",
        "database_path": tmp_path / "test.db",
        # Behavior flags pinned to code defaults — a developer's local .env
        # (e.g. OPEN_DOCS_PRS=false while testing) must not change the suite.
        "open_docs_prs": True,
        "max_fix_attempts": 2,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path)))


class FakeDriftModel:
    """Deterministic judge + fixer so graph and harness plumbing is testable
    offline. Judge: release-notes pages are historical, a word-boundary hit on
    the fact's old identifier is drifted, anything else accurate. Fixer: a
    mechanical old->new replacement (which cannot fix 'removed' facts — that
    failure path is exactly what the rejection tests rely on)."""

    model_name = "fake-drift-model"

    def judge(self, fact, chunks):
        verdicts = []
        for chunk in chunks:
            if "release-notes" in chunk.doc_path:
                verdict = "historical"
            elif fact.old and re.search(
                rf"\b{re.escape(fact.old.strip(chr(39)))}\b", chunk.text
            ):
                verdict = "drifted"
            else:
                verdict = "accurate"
            verdicts.append(
                SectionVerdict(chunk_id=chunk.chunk_id, verdict=verdict, reason=f"fake {verdict}")
            )
        return verdicts

    def propose_fix(self, chunk, facts, feedback=None):
        text = chunk.text
        for fact in facts:
            if fact.kind in ("renamed", "default_changed") and fact.old and fact.new:
                text = re.sub(rf"\b{re.escape(fact.old)}\b", fact.new, text)
        return text


def sign(body: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def merged_pr_payload(number: int = 42) -> dict:
    return {
        "action": "closed",
        "number": number,
        "pull_request": {
            "merged": True,
            "title": "feat: rename timeout to request_timeout",
            "user": {"login": "abdesslem"},
            "additions": 12,
            "deletions": 4,
            "changed_files": 3,
        },
        "repository": {"full_name": "abdesslem/driftguard-demo"},
    }


def post_webhook(
    client: TestClient,
    payload: dict,
    *,
    event: str = "pull_request",
    delivery_id: str = "delivery-1",
    signature: str | None = None,
):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature if signature is not None else sign(body),
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
