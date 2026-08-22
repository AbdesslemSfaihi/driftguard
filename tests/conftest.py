import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from driftguard.api.webhook import create_app
from driftguard.config import Settings

TEST_SECRET = "test-secret"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(github_webhook_secret=TEST_SECRET, database_path=tmp_path / "test.db")
    return TestClient(create_app(settings))


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
