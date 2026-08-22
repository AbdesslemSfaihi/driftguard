# Tutorial: your first pyconnect script

## Step 1 — connect

```python
from pyconnect.client import connect

session = connect("api.example.com", timeout=10)
```

We pass the host and keep the default `timeout` of 10 seconds.

## Step 2 — inspect a URL

Before requesting, you can split any URL into its parts with `parse_url`:

```python
from pyconnect.utils import parse_url

parts = parse_url("https://api.example.com/items")
print(parts["host"])  # api.example.com
```

## Step 3 — make requests

```python
from pyconnect.client import request

response = request("GET", "https://api.example.com/items")
```

## Step 4 — clean up

Always close your session when the work is done:

```python
from pyconnect.client import close

close(session)
```

Leaving sessions open leaks server-side resources.
