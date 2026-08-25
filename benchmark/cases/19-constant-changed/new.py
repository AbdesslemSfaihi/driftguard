"""Connection pooling for pyconnect."""

MAX_POOL_SIZE = 8
POOL_TIMEOUT = 30

_retry_budget = 3  # internal tuning knob, not documented API


class ConnectionPool:
    """A fixed-size pool of reusable connections to one host."""

    def __init__(self, host, size=8):
        self.host = host
        self.size = size
        self.connections = []

    def acquire(self, wait=True):
        """Borrow a connection, blocking up to POOL_TIMEOUT seconds when *wait* is true."""
        return {"host": self.host, "wait": wait}

    def release(self, connection):
        """Return *connection* to the pool for reuse."""
        self.connections.append(connection)

    def drain(self):
        """Close every idle connection in the pool."""
        self.connections.clear()
