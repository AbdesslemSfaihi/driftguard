from conftest import merged_pr_payload, post_webhook


def test_merged_pr_is_accepted(client):
    response = post_webhook(client, merged_pr_payload())
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_invalid_signature_is_rejected(client):
    response = post_webhook(client, merged_pr_payload(), signature="sha256=" + "0" * 64)
    assert response.status_code == 401


def test_duplicate_delivery_is_a_noop(client):
    first = post_webhook(client, merged_pr_payload(), delivery_id="dup-1")
    second = post_webhook(client, merged_pr_payload(), delivery_id="dup-1")
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"


def test_closed_but_not_merged_pr_is_ignored(client):
    payload = merged_pr_payload()
    payload["pull_request"]["merged"] = False
    response = post_webhook(client, payload)
    assert response.json()["status"] == "ignored"


def test_other_events_are_ignored(client):
    response = post_webhook(client, {"action": "created"}, event="issue_comment")
    assert response.json()["status"] == "ignored"


def test_ping_gets_pong(client):
    response = post_webhook(client, {"zen": "Keep it logically awesome."}, event="ping")
    assert response.json()["status"] == "pong"


def test_missing_delivery_id_is_rejected(client):
    import json

    from conftest import sign

    body = json.dumps(merged_pr_payload()).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(body),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_signed_but_malformed_json_is_a_400_not_a_500(client):
    from conftest import sign

    body = b"{not json"
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(body),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "bad-json-1",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_pending_deliveries_are_recovered(tmp_path):
    """A delivery accepted before a crash sits at 'received'; recovery re-runs it."""
    from driftguard.agent.handler import recover_pending
    from driftguard.config import Settings
    from driftguard.storage import db

    settings = Settings(github_webhook_secret="s", database_path=tmp_path / "recover.db")
    db.record_delivery(
        settings.database_path,
        delivery_id="lost-1",
        event="pull_request",
        action="closed",
        repo="abdesslem/driftguard-demo",
        pr_number=1,
        payload=merged_pr_payload(),
    )

    assert recover_pending(settings) == 1
    # No GITHUB_TOKEN in these settings -> the handler archives and stops.
    assert db.get_delivery(settings.database_path, "lost-1")["status"] == "logged"
    assert recover_pending(settings) == 0  # nothing pending twice


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
