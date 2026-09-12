"""Processing a developer's answer — the resume half of ask-the-developer.

The "pause" between ask and answer is not held in memory: the ask node stored
each question in SQLite and this module handles the answer as a brand-new
delivery (an issue_comment webhook), reloading everything it needs from the
questions table. A process restart between the two costs nothing.

Authority is structural, not prompted: the judge is never called here. The
maintainer's reply IS the verdict; the model appears only to (a) read the
free-text reply into a typed resolution — a translator, never an overruler —
and (b) write the fix. Verification stays deterministic code, because human
authority covers the judgment, not the quality of generated prose.

Time moved while we waited: the doc is re-fetched at the CURRENT default
branch (the original docs PR may have merged since) and the section is
re-located by heading, not by stale line numbers. A section that no longer
exists marks the question 'stale' — reported, never guessed at.
"""

import json
import logging
from dataclasses import dataclass, field
from sqlite3 import Row

from driftguard.agent.asking import parse_answer_lines
from driftguard.agent.graph import Proposal
from driftguard.agent.model import DriftModel
from driftguard.agent.verify import verify_fix
from driftguard.analysis.extractor import DriftFact
from driftguard.config import Settings
from driftguard.github.client import GitHubClient
from driftguard.indexing.chunker import DocChunk, apply_chunk_edits, chunk_markdown
from driftguard.storage import db

log = logging.getLogger("driftguard.agent")

# Proposal outcome -> question outcome. "verified" also resolves the question:
# with OPEN_DOCS_PRS off, the stored verified proposal IS the deliverable.
_QUESTION_STATUS = {
    "published": "resolved_fixed",
    "verified": "resolved_fixed",
    "rejected": "fix_failed",
    "stale": "stale",
}


@dataclass
class AnswersResult:
    status: str  # answers_processed | no_answers_matched
    pr_url: str | None = None
    proposals: list[Proposal] = field(default_factory=list)


def process_answers(
    settings: Settings,
    client: GitHubClient,
    model: DriftModel,
    *,
    delivery_id: str,
    repo: str,
    pr_number: int,
    comment_body: str,
    author: str,
) -> AnswersResult:
    answers = parse_answer_lines(comment_body)
    open_rows = db.open_questions(settings.database_path, repo, pr_number)
    open_by_label = {row["q_label"]: row for row in open_rows}
    matched = {label: text for label, text in answers.items() if label in open_by_label}
    if not matched:
        log.info(
            "delivery %s: comment matched no open question on %s#%s", delivery_id, repo, pr_number
        )
        return AnswersResult(status="no_answers_matched")

    confirmed: list[tuple[Row, DriftFact, str]] = []
    for label, answer_text in sorted(matched.items()):
        row = open_by_label[label]
        fact = DriftFact(**json.loads(row["fact_json"]))
        reading = model.interpret_answer(row["question"], fact, answer_text)
        log.info(
            "delivery %s: %s read as %s (%s)",
            delivery_id,
            label,
            reading.resolution,
            reading.summary,
        )
        if reading.resolution == "confirms_accurate":
            db.resolve_question(
                settings.database_path,
                row["id"],
                status="resolved_accurate",
                answer=answer_text,
                answered_by=author,
            )
        elif reading.resolution == "confirms_drift":
            confirmed.append((row, fact, answer_text))
        else:  # unclear — stays open so a follow-up comment can still answer it
            log.info(
                "delivery %s: %s reply did not answer the question — left open", delivery_id, label
            )

    if not confirmed:
        return AnswersResult(status="answers_processed")

    proposals = _fix_confirmed(
        settings, client, model, repo=repo, confirmed=confirmed, author=author
    )
    pr_url = _publish(
        settings,
        client,
        repo=repo,
        pr_number=pr_number,
        delivery_id=delivery_id,
        proposals=proposals,
    )

    for (row, _, answer_text), proposal in zip(confirmed, proposals, strict=True):
        db.resolve_question(
            settings.database_path,
            row["id"],
            status=_QUESTION_STATUS[proposal.status],
            answer=answer_text,
            answered_by=author,
        )
    return AnswersResult(status="answers_processed", pr_url=pr_url, proposals=proposals)


def _fix_confirmed(
    settings: Settings,
    client: GitHubClient,
    model: DriftModel,
    *,
    repo: str,
    confirmed: list[tuple[Row, DriftFact, str]],
    author: str,
) -> list[Proposal]:
    """fix -> verify with bounded retries, one proposal per confirmed question.

    A plain loop, not a second LangGraph: this path is linear (no judge, no
    branching beyond the retry), and a graph would only obscure that.
    """
    default_branch = client.default_branch(repo)
    proposals: list[Proposal] = []
    for row, fact, answer_text in confirmed:
        chunk = _relocate_section(
            client, repo, default_branch, row["doc_path"], row["heading"], fact
        )
        if chunk is None:
            log.warning(
                "question %s: section %r in %s no longer exists at %s — marked stale",
                row["q_label"],
                row["heading"],
                row["doc_path"],
                default_branch,
            )
            stale = Proposal(chunk=_missing_chunk(row), facts=[fact])
            stale.status = "stale"
            proposals.append(stale)
            continue

        proposal = Proposal(
            chunk=chunk,
            facts=[fact],
            reasons=[f"maintainer @{author} confirmed: {answer_text}"],
        )
        while proposal.status == "pending":
            proposal.new_text = model.propose_fix(
                chunk, [fact], feedback=proposal.feedback, developer_note=answer_text
            )
            proposal.attempts += 1
            feedback = verify_fix([fact], chunk.text, proposal.new_text)
            if feedback is None:
                proposal.status = "verified"
            elif proposal.attempts >= settings.max_fix_attempts:
                proposal.status = "rejected"
                proposal.feedback = feedback
                log.warning(
                    "question %s: fix rejected after %d attempts: %s",
                    row["q_label"],
                    proposal.attempts,
                    feedback,
                )
            else:
                proposal.feedback = feedback
        proposals.append(proposal)
    return proposals


def _publish(
    settings: Settings,
    client: GitHubClient,
    *,
    repo: str,
    pr_number: int,
    delivery_id: str,
    proposals: list[Proposal],
) -> str | None:
    verified = [p for p in proposals if p.status == "verified"]
    if not verified:
        return None
    if not settings.open_docs_prs:
        log.info(
            "OPEN_DOCS_PRS is off — %d verified follow-up proposal(s) stored only", len(verified)
        )
        return None

    default_branch = client.default_branch(repo)
    base_sha = client.ref_sha(repo, default_branch)
    # Suffixed with the delivery id: the same PR can receive several answer
    # comments over time, and each batch needs its own branch.
    branch = f"driftguard/docs-pr-{pr_number}-answers-{delivery_id[:8]}"
    client.create_branch(repo, branch, base_sha)

    by_doc: dict[str, list[Proposal]] = {}
    for proposal in verified:
        by_doc.setdefault(proposal.chunk.doc_path, []).append(proposal)
    for doc_path, doc_proposals in sorted(by_doc.items()):
        result = client.file_with_sha(repo, doc_path, default_branch)
        if result is None:  # existed during _fix_confirmed; racing deletes are possible
            log.warning("%s disappeared before publish — skipping its edits", doc_path)
            continue
        text, sha = result
        new_doc = apply_chunk_edits(text, [(p.chunk, p.new_text) for p in doc_proposals])
        client.put_file(
            repo,
            doc_path,
            message=f"docs: update {doc_path} per maintainer answer on #{pr_number}",
            text=new_doc,
            branch=branch,
            sha=sha,
        )

    url = client.open_pull_request(
        repo,
        title=f"docs: fix drift confirmed by a maintainer on #{pr_number}",
        body=_pr_body(pr_number, verified),
        head=branch,
        base=default_branch,
    )
    for proposal in verified:
        proposal.status = "published"
    log.info("opened follow-up docs PR: %s", url)
    return url


def _relocate_section(
    client: GitHubClient,
    repo: str,
    default_branch: str,
    doc_path: str,
    heading: str,
    fact: DriftFact,
) -> DocChunk | None:
    """Find the stored section in TODAY'S version of the doc.

    Matching is by heading breadcrumb: line numbers recorded weeks ago are
    meaningless after other edits merged. Among several windows under the same
    heading, prefer the one still mentioning the fact's old value — that is
    the text the question was about."""
    result = client.file_with_sha(repo, doc_path, default_branch)
    if result is None:
        return None
    candidates = [c for c in chunk_markdown(result[0], doc_path) if c.heading_path == heading]
    if not candidates:
        return None
    if fact.old:
        for chunk in candidates:
            if fact.old.strip("\"'") in chunk.text:
                return chunk
    return candidates[0]


def _missing_chunk(row: Row) -> DocChunk:
    """Placeholder chunk for a stale question, so its Proposal row still records
    where the section USED to be."""
    return DocChunk(
        doc_path=row["doc_path"],
        heading_path=row["heading"],
        text="",
        chunk_id=f"{row['doc_path']}::stale",
        start_line=0,
        end_line=0,
    )


def _pr_body(pr_number: int, proposals: list[Proposal]) -> str:
    lines = [
        f"After #{pr_number} merged, DriftGuard could not decide whether the"
        " sections below were contradicted, so it asked on the PR. A maintainer"
        " answered; every edit below applies that answer and passed"
        " deterministic verification.",
        "",
    ]
    for proposal in proposals:
        lines.append(f"### `{proposal.chunk.doc_path}` — {proposal.chunk.heading_path}")
        for fact, reason in zip(proposal.facts, proposal.reasons, strict=True):
            lines.append(f"- **{fact.kind}** `{fact.symbol}`: {reason}")
        lines.append("")
    lines.append("---")
    lines.append(
        f"*Opened automatically by DriftGuard from a maintainer's answer on #{pr_number}.*"
    )
    return "\n".join(lines)
