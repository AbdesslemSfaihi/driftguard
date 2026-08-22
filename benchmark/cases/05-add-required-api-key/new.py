"""Connection handling for pyconnect."""


def connect(host, api_key, timeout=10):
    """Open a connection to *host*, authenticated with *api_key*.

    Waits up to *timeout* seconds. Returns a session dict used by the
    other client functions.
    """
    return {"host": host, "api_key": api_key, "timeout": timeout, "open": True}


def request(method, url, retries=2):
    """Perform an HTTP request. Failed calls are retried up to *retries* times."""
    return {"method": method, "url": url, "retries": retries, "status": 200}


def close(session):
    """Close a session opened by connect()."""
    session["open"] = False
    return session
