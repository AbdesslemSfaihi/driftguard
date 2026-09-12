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


def test_proposals_roundtrip_and_replace_on_rerun(tmp_path):
    from driftguard.agent.graph import Proposal
    from driftguard.indexing.chunker import DocChunk

    chunk = DocChunk(
        doc_path="docs/config.md",
        heading_path="Configuration > Timeouts",
        text="old section",
        chunk_id="docs/config.md::2",
        start_line=4,
        end_line=6,
    )
    fact = FakeFact()
    proposal = Proposal(
        chunk=chunk, facts=[fact], reasons=["says timeout"], new_text="new section",
        status="verified",
    )

    path = tmp_path / "p.db"
    record(path)
    db.save_proposals(path, "d-1", [proposal])
    db.save_proposals(path, "d-1", [proposal])  # replay must not duplicate

    rows = db.proposals_for_delivery(path, "d-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["doc_path"] == "docs/config.md"
    assert (row["start_line"], row["end_line"]) == (4, 6)
    assert row["facts"] == "renamed:connect.timeout"
    assert row["status"] == "verified"

    db.set_docs_pr_url(path, "d-1", "https://github.com/a/b/pull/9")
    assert db.get_delivery(path, "d-1")["docs_pr_url"] == "https://github.com/a/b/pull/9"

def test_replaying_a_delivery_never_clobbers_an_answered_question(tmp_path):
    """save_questions is INSERT OR IGNORE, not delete+insert like facts and
    proposals: a recorded answer is the one thing a replay may not redo."""
    path = tmp_path / "questions.db"
    record(path)
    question = ("Q1", "{}", "docs/x.md", "X", "which function?")

    db.save_questions(path, "d-1", repo="a/b", pr_number=1, questions=[question])
    db.resolve_question(
        path,
        db.questions_for_delivery(path, "d-1")[0]["id"],
        status="resolved_accurate",
        answer="no, still fine",
        answered_by="maria",
    )
    # The replayed run tries to save the same question again.
    db.save_questions(path, "d-1", repo="a/b", pr_number=1, questions=[question])

    rows = db.questions_for_delivery(path, "d-1")
    assert len(rows) == 1
    assert rows[0]["status"] == "resolved_accurate"
    assert rows[0]["answer"] == "no, still fine"
    assert not db.has_open_questions(path, "a/b", 1)


def test_verdicts_roundtrip_and_replace_on_rerun(tmp_path):
    from driftguard.agent.graph import SectionJudgment
    from driftguard.indexing.chunker import DocChunk

    chunk = DocChunk("docs/faq.md", "FAQ > Retries", "text", "docs/faq.md::1", 2, 5)
    judgment = SectionJudgment(FakeFact(), chunk, 1, None, "unsure", "names no function", "Which?")
    path = tmp_path / "v.db"
    record(path)
    db.save_verdicts(path, "d-1", [judgment])
    db.save_verdicts(path, "d-1", [judgment])  # replay converges, never duplicates

    rows = db.verdicts_for_delivery(path, "d-1")
    assert len(rows) == 1
    row = rows[0]
    assert (row["fact"], row["verdict"], row["question"]) == (
        "renamed:connect.timeout",
        "unsure",
        "Which?",
    )
    assert (row["dense_rank"], row["sparse_rank"]) == (1, None)


def test_proposals_keep_attempts_and_verifier_feedback(tmp_path):
    from driftguard.agent.graph import Proposal
    from driftguard.indexing.chunker import DocChunk

    chunk = DocChunk("docs/x.md", "X", "old", "docs/x.md::0", 0, 0)
    proposal = Proposal(chunk=chunk, facts=[FakeFact()], reasons=["r"], new_text="old")
    proposal.status, proposal.attempts, proposal.feedback = "rejected", 2, "nothing was changed"
    path = tmp_path / "p2.db"
    record(path)
    db.save_proposals(path, "d-1", [proposal])

    row = db.proposals_for_delivery(path, "d-1")[0]
    assert (row["attempts"], row["feedback"]) == (2, "nothing was changed")
