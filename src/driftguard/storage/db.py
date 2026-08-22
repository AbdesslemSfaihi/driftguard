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

SCHEMA_V1 = """
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

CREATE TABLE IF NOT EXISTS drift_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
    kind        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    old         TEXT,
    new         TEXT,
    file        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# V2: the agent's verified doc fixes, one row per (delivery, doc section).
# Kept even after a PR is opened — they are the evidence trail.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
    doc_path    TEXT NOT NULL,
    heading     TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    old_text    TEXT NOT NULL,
    new_text    TEXT NOT NULL,
    facts       TEXT NOT NULL,      -- "renamed:connect.timeout, default_changed:..."
    reasons     TEXT NOT NULL,      -- the judge's evidence, one line per fact
    status      TEXT NOT NULL,      -- verified | rejected | published
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE deliveries ADD COLUMN docs_pr_url TEXT;
"""

# MIGRATIONS[i] upgrades a database at user_version i to version i+1.
# Append-only: never edit an entry that has shipped — add a new script instead.
# V1 keeps IF NOT EXISTS so databases created before version tracking (they
# have the tables but user_version == 0) upgrade in place without errors.
MIGRATIONS: tuple[str, ...] = (SCHEMA_V1, SCHEMA_V2)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # user_version is SQLite's built-in schema-version slot: one integer read
    # per connection when up to date, and each migration + version bump commits
    # together so a crash cannot record a migration it did not finish.
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
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


def pending_deliveries(db_path: Path) -> list[str]:
    """Ids of deliveries accepted (202) but never finished, oldest first.

    BackgroundTasks die with the process; anything still at 'received' after a
    restart is exactly the work that was lost. The archived payload is the queue
    entry, so recovery is just re-processing these.
    """
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT delivery_id FROM deliveries WHERE status = 'received' ORDER BY received_at"
        ).fetchall()
        return [row["delivery_id"] for row in rows]


def set_status(db_path: Path, delivery_id: str, status: str) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE deliveries SET status = ? WHERE delivery_id = ?", (status, delivery_id)
        )


def save_facts(db_path: Path, delivery_id: str, facts: list[Any]) -> None:
    """Persist extracted drift facts (accepts DriftFact instances).

    Replaces any earlier extraction for the same delivery: startup recovery and
    the replay CLI may re-run a delivery, and re-running must converge on the
    same rows, not append duplicates. Delete + insert in ONE transaction, so a
    crash mid-way never leaves the delivery with zero facts recorded as done.
    """
    with closing(_connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM drift_facts WHERE delivery_id = ?", (delivery_id,))
        conn.executemany(
            "INSERT INTO drift_facts (delivery_id, kind, symbol, old, new, file)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(delivery_id, f.kind, f.symbol, f.old, f.new, f.file) for f in facts],
        )


def facts_for_delivery(db_path: Path, delivery_id: str) -> list[sqlite3.Row]:
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT kind, symbol, old, new, file FROM drift_facts"
            " WHERE delivery_id = ? ORDER BY id",
            (delivery_id,),
        ).fetchall()


def save_proposals(db_path: Path, delivery_id: str, proposals: list[Any]) -> None:
    """Persist the agent's doc-fix proposals (accepts Proposal instances).

    Same replace-on-rerun contract as save_facts: recovery and replay must
    converge on the same rows.
    """
    with closing(_connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM proposals WHERE delivery_id = ?", (delivery_id,))
        conn.executemany(
            "INSERT INTO proposals (delivery_id, doc_path, heading, start_line, end_line,"
            " old_text, new_text, facts, reasons, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    delivery_id,
                    p.chunk.doc_path,
                    p.chunk.heading_path,
                    p.chunk.start_line,
                    p.chunk.end_line,
                    p.chunk.text,
                    p.new_text,
                    ", ".join(f"{f.kind}:{f.symbol}" for f in p.facts),
                    "\n".join(p.reasons),
                    p.status,
                )
                for p in proposals
            ],
        )


def proposals_for_delivery(db_path: Path, delivery_id: str) -> list[sqlite3.Row]:
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT doc_path, heading, start_line, end_line, old_text, new_text,"
            " facts, reasons, status FROM proposals WHERE delivery_id = ? ORDER BY id",
            (delivery_id,),
        ).fetchall()


def set_docs_pr_url(db_path: Path, delivery_id: str, url: str) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE deliveries SET docs_pr_url = ? WHERE delivery_id = ?", (url, delivery_id)
        )
