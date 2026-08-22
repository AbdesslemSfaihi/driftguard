"""SQLite persistence.

Design choices worth defending:
- Connections are short-lived (opened per operation). SQLite connections are cheap
  and this sidesteps thread-affinity issues with FastAPI's threadpool and
  background tasks. WAL mode lets readers and the writer coexist.
- `deliveries.delivery_id` is the PRIMARY KEY: GitHub redelivers webhooks on
  timeouts, so idempotency is enforced by the database itself (INSERT OR IGNORE),
  not by application logic that can race.
- The raw payload is archived verbatim: it powers the replay CLI (tests, debugging,
  and the offline demo fallback).
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id  TEXT PRIMARY KEY,
    event        TEXT NOT NULL,
    action       TEXT,
    repo         TEXT,
    pr_number    INTEGER,
    status       TEXT NOT NULL DEFAULT 'received',
    payload_json TEXT NOT NULL,
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record_delivery(
    db_path: Path,
    *,
    delivery_id: str,
    event: str,
    action: str | None,
    repo: str | None,
    pr_number: int | None,
    payload: dict[str, Any],
) -> bool:
    """Archive one webhook delivery. Returns False if it was already recorded."""
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO deliveries"
            " (delivery_id, event, action, repo, pr_number, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (delivery_id, event, action, repo, pr_number, json.dumps(payload)),
        )
        return cur.rowcount == 1


def get_delivery(db_path: Path, delivery_id: str) -> sqlite3.Row | None:
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()


def list_deliveries(db_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT delivery_id, event, action, repo, pr_number, status, received_at"
            " FROM deliveries ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def set_status(db_path: Path, delivery_id: str, status: str) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE deliveries SET status = ? WHERE delivery_id = ?", (status, delivery_id)
        )
