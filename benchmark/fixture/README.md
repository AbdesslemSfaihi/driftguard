# pyconnect

A tiny HTTP connection library used as the DriftGuard benchmark fixture.

## Features

- Simple connection management with sensible defaults
- Automatic retries with exponential backoff
- Token-based authentication, including legacy servers

## Quickstart

```python
from pyconnect.client import connect, request, close

session = connect("api.example.com", timeout=10)
response = request("GET", "https://api.example.com/items")
close(session)
```

The `timeout` parameter of `connect` defaults to 10 seconds.

## License

MIT. See LICENSE for details.
