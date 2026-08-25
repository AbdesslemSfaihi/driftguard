# Connection pools

## Creating a pool

`ConnectionPool(host, size=8)` manages reusable connections to one host. The
pool holds at most `MAX_POOL_SIZE` (**8**) connections:

```python
from pyconnect.pool import ConnectionPool

pool = ConnectionPool("api.example.com", size=8)
```

## Borrowing and returning connections

`acquire(wait=True)` borrows a connection. When the pool is empty it blocks
for up to `POOL_TIMEOUT` seconds — **15** by default — before giving up.
`release(connection)` hands the connection back for reuse:

```python
conn = pool.acquire(wait=True)
try:
    ...
finally:
    pool.release(conn)
```

## Draining a pool

Call `drain()` to close every idle connection, for example before shutdown:

```python
pool.drain()
```
