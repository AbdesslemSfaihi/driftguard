"""Authentication helpers for pyconnect."""


def login(username, password):
    """Authenticate and return an access token."""
    return {"user": username, "token": f"tok-{username}", "password_ok": bool(password)}


def refresh_token(token, ttl=3600):
    """Exchange *token* for a fresh one valid for *ttl* seconds."""
    return {"token": f"{token}-refreshed", "expires_in": ttl}
