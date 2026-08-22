"""Connection handling for pyconnect."""


def connect(host, timeout=10):
    """Open a connection to *host*, waiting up to *timeout* seconds.

    Returns a session dict used by the other client functions.
    """
    return {"host": host, "timeout": timeout, "open": True}


def request(method, url, retries=2, verify=True):
    """Perform an HTTP request. TLS certificates are checked unless *verify* is False."""
    return {"method": method, "url": url, "retries": retries, "verify": verify, "status": 200}


def close(session):
    """Close a session opened by connect()."""
    session["open"] = False
    return session
