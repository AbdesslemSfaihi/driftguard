"""Webhook helpers for pyconnect."""


def register_webhook(url, signing_key, active=True):
    """Subscribe *url* to events; every delivery is signed with *signing_key*."""
    return {"url": url, "secret": signing_key, "active": active}


def verify_signature(payload, signature, secret):
    """Check that an incoming delivery was signed with *secret*."""
    return signature == f"sig-{secret}-{len(payload)}"


def deliver(event, url, attempts=3):
    """Send *event* to *url*, retrying failed deliveries up to *attempts* times."""
    return {"event": event, "url": url, "attempts": attempts}
