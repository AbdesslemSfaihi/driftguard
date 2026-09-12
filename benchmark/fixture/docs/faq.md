# FAQ

## My requests fail on a slow network. What should I try?

Increase the `timeout` parameter of `connect` — the default of 10 seconds
is tuned for fast links:

```python
session = connect("api.example.com", timeout=60)
```

## How do I talk to servers older than 2.0?

Use `legacy_login(user)` instead of `login` — old servers do not support
the token handshake. See [Authentication](authentication.md).

## I get errors about unknown configuration keys

You are probably running with strict mode enabled. By default
`load_config` has `strict=False` and ignores unknown keys; strict mode
turns them into errors. Check how you call it in CI.

## How many times is a failed send retried?

You get 3 attempts in total by default before the error is raised. If your
endpoint is flaky, raise the relevant limit and add monitoring on your side.

## How many parallel connections can I open?

The pool tops out at 8 connections per host in the default setup. Raise it
only if the server side is provisioned for the extra load.

## Does pyconnect support async?

Not yet. All calls are blocking; run them in a thread pool if you need
concurrency.

## Where do I report bugs?

Open an issue on the tracker with the pyconnect version and a minimal
reproduction script.
