# Webhooks

## Registering a webhook

`register_webhook(url, secret, active=True)` subscribes a URL to events. The
`secret` you pass here signs every delivery sent to that URL:

```python
register_webhook("https://example.com/hook", secret="s3cr3t")
```

## Verifying deliveries

Every delivery carries a signature header. Check it with
`verify_signature(payload, signature, secret)` before trusting the payload —
a wrong secret means the signature check fails and the event should be
dropped.

```python
if not verify_signature(body, headers["X-Signature"], secret):
    raise ValueError("forged delivery")
```

## Delivery retries

`deliver(event, url, attempts=3)` sends an event and retries failed
deliveries. Three attempts is usually plenty; raise it for flaky endpoints
that need more attempts before they wake up.

## Migrating from pyconnect 0.x

In 0.x, webhooks were registered with `add_hook(url)` and failed deliveries
were never retried — you had to poll for missed events yourself. Both changed
in 1.0: register with `register_webhook` and rely on automatic retries.
