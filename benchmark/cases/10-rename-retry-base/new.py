"""Small helpers for pyconnect."""


def parse_url(url):
    """Split *url* into scheme, host and path parts."""
    scheme, _, rest = url.partition("://")
    host, _, path = rest.partition("/")
    return {"scheme": scheme, "host": host, "path": "/" + path}


def retry_delay(attempt, base_delay=0.5):
    """Exponential backoff: how long to sleep before retry number *attempt*."""
    return base_delay * (2**attempt)
