# Configuration

## Connection timeouts

The `timeout` parameter of `connect()` controls how long the client waits
for the server before giving up. The default is **10 seconds**:

```python
session = connect("api.example.com", timeout=10)
```

Raise it for slow networks, lower it for latency-sensitive services.

## Strict mode

`load_config(path, strict=False)` reads your configuration file. Strict mode
is **off by default**: unknown keys are ignored. Pass `strict=True` to make
unknown keys raise an error instead — recommended in CI.

```python
config = load_config("pyconnect.toml", strict=True)
```

## Saving configuration

Use `save_config(config, path)` to write changes back to disk.

## Environment variables

`PYCONNECT_HOME` overrides where pyconnect looks for its default
configuration file. This is rarely needed.
