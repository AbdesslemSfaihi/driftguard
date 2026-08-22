# Authentication

## Logging in

```python
from pyconnect.auth import login

token = login("alice", "s3cret")
```

`login` returns an access token used by all authenticated requests.

## Token refresh

Tokens expire. Use `refresh_token(token, ttl=3600)` to obtain a fresh one —
by default the new token is valid for **3600 seconds (one hour)**:

```python
fresh = refresh_token(token)
fresh = refresh_token(token, ttl=1800)  # shorter-lived token
```

## Legacy authentication

Servers older than 2.0 do not support the token handshake. For those, call
`legacy_login(user)` instead of `login`:

```python
token = legacy_login("alice")
```

Legacy mode sends no password — access control happens on the server side.

## Security notes

Never hard-code credentials. Read them from the environment or a secrets
manager, and rotate tokens regularly.
