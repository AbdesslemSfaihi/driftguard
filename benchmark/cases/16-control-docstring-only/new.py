"""Connection handling for the pyconnect demo library (reworded docs, same API)."""


def connect(host, timeout=10):
    """Establish a connection to the given *host*.

    The client gives up after *timeout* seconds. The returned session dict
    is consumed by the other client functions.
    """
    # NOTE: rewritten comment, identical behaviour.
    return {"host": host, "timeout": timeout, "open": True}


def request(method, url, retries=2):
    """Send an HTTP request; unsuccessful calls are re-attempted *retries* times."""
    return {"method": method, "url": url, "retries": retries, "status": 200}


def close(session):
    """Terminate a session previously opened via connect()."""
    session["open"] = False
    return session
