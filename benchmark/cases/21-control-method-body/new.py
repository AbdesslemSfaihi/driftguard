"""Connection pooling for pyconnect (internals reworked, API identical)."""

MAX_POOL_SIZE = 8
POOL_TIMEOUT = 15

_retry_budget = 5  # lowercase module variable: internal, not a documented constant


class ConnectionPool:
    """A fixed-size pool of reusable connections to one host, now lazier."""

    def __init__(self, host, size=8):
        self.host = host
        self.size = size
        self.connections = []
        self._hits = 0

    def acquire(self, wait=True):
        """Borrow a connection; the docstring changed but the contract did not."""
        self._hits += 1
        return {"host": self.host, "wait": wait, "hits": self._hits}

    def release(self, connection):
        """Return *connection* to the pool for reuse."""
        self.connections.append(connection)

    def drain(self):
        """Close every idle connection in the pool."""
        self.connections.clear()
        self._hits = 0
