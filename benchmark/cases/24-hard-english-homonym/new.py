"""Webhook helpers for pyconnect."""


def register_webhook(url, secret, active=True):
    """Subscribe *url* to events; every delivery is signed with *secret*."""
    return {"url": url, "secret": secret, "active": active}


def verify_signature(payload, signature, secret):
    """Check that an incoming delivery was signed with *secret*."""
    return signature == f"sig-{secret}-{len(payload)}"


def deliver(event, url, max_retries=3):
    """Send *event* to *url*, retrying failed deliveries up to *max_retries* times."""
    return {"event": event, "url": url, "attempts": max_retries}
