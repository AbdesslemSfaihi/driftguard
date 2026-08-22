# Error handling

## Automatic retries

`request()` retries failed calls automatically. The `retries` parameter
defaults to **2**, so a request is attempted at most 3 times in total:

```python
response = request("GET", url, retries=2)
```

Set `retries=0` to disable the mechanism entirely.

## Backoff strategy

Between attempts, pyconnect sleeps according to `retry_delay(attempt, base=0.5)`
— exponential backoff starting at *base* seconds (default 0.5): 0.5s, 1s, 2s...

```python
sleep_for = retry_delay(attempt=2)  # 2.0 seconds
```

## Timeout errors

A connection that exceeds its timeout raises a `TimeoutError`. See
[Configuration](configuration.md) for tuning the timeout itself.

## What to log

Log the URL, the attempt number and the final status. Do not log tokens
or request bodies containing personal data.
