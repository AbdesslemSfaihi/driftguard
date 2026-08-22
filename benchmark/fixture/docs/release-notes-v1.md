# Release notes — 1.x (historical)

This page describes past releases. It is a historical record: the facts
below were true for the versions they describe and must not be edited to
match newer code.

## 1.2.0

- Added `legacy_login(user)` for pre-2.0 servers.
- `refresh_token` gained the `ttl` parameter (default 3600 seconds).

## 1.1.0

- Added automatic retries to `request` (default `retries=2`).
- Added `retry_delay` with exponential backoff.

## 1.0.0

- First stable release.
- `connect(host, timeout=10)` introduced with a 10-second default timeout.
- Configuration loading via `load_config(path, strict=False)`.
