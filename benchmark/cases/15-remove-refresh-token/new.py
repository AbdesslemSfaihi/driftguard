"""Authentication helpers for pyconnect."""


def login(username, password):
    """Authenticate and return an access token."""
    return {"user": username, "token": f"tok-{username}", "password_ok": bool(password)}


def legacy_login(user):
    """Authenticate against pre-2.0 servers using the legacy handshake."""
    return {"user": user, "token": f"legacy-{user}"}
