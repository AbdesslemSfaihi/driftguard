# Quickstart

## Your first connection

```python
from pyconnect.client import connect, request

session = connect("api.example.com", timeout=10)
print(session["open"])  # True
```

`connect` takes the host name and an optional `timeout` in seconds.

## Making requests

```python
response = request("GET", "https://api.example.com/items")
print(response["status"])
```

## Where to go next

- [Configuration](configuration.md) for timeouts and strict mode
- [Authentication](authentication.md) for logging in
- [Error handling](error-handling.md) for retries and backoff
