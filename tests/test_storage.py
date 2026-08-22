"""Storage-level guarantees: versioned migrations, idempotent fact writes."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from driftguard.storage import db


def record(db_path: Path, delivery_id: str = "d-1") -> None:
    db.record_delivery(
        db_path,
        delivery_id=delivery_id,
        event="pull_request",
        action="closed",
        repo="a/b",
        pr_number=1,
        payload={},
    )


def user_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


@dataclass
class FakeFact:
    kind: str = "renamed"
    symbol: str = "connect.timeout"
    old: str = "timeout"
    new: str = "request_timeout"
    file: str = "client.py"


def test_fresh_database_is_stamped_with_the_latest_version(tmp_path):
    path = tmp_path / "fresh.db"
    record(path)
    assert user_version(path) == len(db.MIGRATIONS)


def test_pre_versioning_database_is_upgraded_in_place(tmp_path):
    # A database from before user_version tracking: tables exist, version == 0.
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(db.MIGRATIONS[0])
    assert user_version(path) == 0

    record(path)  # must re-apply idempotently and stamp the version
    assert user_version(path) == len(db.MIGRATIONS)


def test_save_facts_replaces_the_previous_extraction(tmp_path):
    path = tmp_path / "facts.db"
    record(path)
    db.save_facts(path, "d-1", [FakeFact()])
    db.save_facts(path, "d-1", [FakeFact()])  # recovery/replay re-run
    assert len(db.facts_for_delivery(path, "d-1")) == 1
