# API reference

## pyconnect.client

### connect(host, timeout=10)

Open a connection to *host*. Waits up to *timeout* seconds (default 10)
before raising a timeout error. Returns a session object.

### request(method, url, retries=2)

Perform an HTTP request. Failed calls are retried automatically up to
*retries* times (default 2).

### close(session)

Close a session created by `connect`. Always call it when you are done.

## pyconnect.auth

### login(username, password)

Authenticate with a username and password. Returns an access token.

### refresh_token(token, ttl=3600)

Exchange a token for a fresh one, valid for *ttl* seconds (default 3600,
one hour).

### legacy_login(user)

Authenticate against pre-2.0 servers using the legacy handshake.

## pyconnect.config

### load_config(path, strict=False)

Read a configuration file. With *strict* enabled, unknown keys raise an
error; by default they are ignored.

### save_config(config, path)

Write a configuration mapping back to disk.

## pyconnect.utils

### parse_url(url)

Split a URL into scheme, host and path parts.

### retry_delay(attempt, base=0.5)

Exponential backoff helper: returns how long to sleep before retry
number *attempt*, starting from *base* seconds (default 0.5).
