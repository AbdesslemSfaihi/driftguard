"""Small helpers for pyconnect."""


def retry_delay(attempt, base=0.5):
    """Exponential backoff: how long to sleep before retry number *attempt*."""
    return base * (2**attempt)
