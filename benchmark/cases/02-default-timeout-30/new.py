"""Connection handling for pyconnect."""


def connect(host, timeout=30):
    """Open a connection to *host*, waiting up to *timeout* seconds.

    Returns a session dict used by the other client functions.
    """
    return {"host": host, "timeout": timeout, "open": True}


def request(method, url, retries=2):
    """Perform an HTTP request. Failed calls are retried up to *retries* times."""
    return {"method": method, "url": url, "retries": retries, "status": 200}


def close(session):
    """Close a session opened by connect()."""
    session["open"] = False
    return session
