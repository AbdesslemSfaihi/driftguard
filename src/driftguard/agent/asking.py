"""The ask-the-developer wire format: one digest comment out, labeled replies in.

Formatting and parsing live in the same module on purpose: the digest teaches
developers to answer as `Q1: ...`, and the parser reads exactly that shape. If
one side changes, the reviewer sees both sides in one diff.

Correlation is by label, not by GitHub reply-threading: PR issue comments have
no reliable threading, so the digest carries stable labels (Q1, Q2...) that the
questions table also stores. A reply line that matches no open label is
IGNORED, never guessed at — same policy as everywhere else in DriftGuard.
"""

import re
from dataclasses import dataclass

from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import DocChunk

# Present in every comment DriftGuard posts; the webhook uses it to ignore
# our own comments instead of processing them as developer answers.
QUESTION_MARKER = "<!-- driftguard:questions -->"

# "Q1: it's about deliver()" / "**Q2** - no, still correct" — one answer per line.
ANSWER_LINE_RE = re.compile(r"^\s*(?:\*\*)?[Qq](\d+)(?:\*\*)?\s*[:\-]\s*(\S.*)$", re.MULTILINE)


@dataclass
class PendingQuestion:
    """One undecidable judgment, escalated instead of guessed."""

    fact: DriftFact
    chunk: DocChunk
    question: str
    label: str = ""  # "Q1" — assigned when the digest is composed


def format_digest(pr_number: int, questions: list[PendingQuestion], fact_lines: list[str]) -> str:
    lines = [
        f"**DriftGuard needs a maintainer's answer.** PR #{pr_number} changed code"
        " that may make the sections below incorrect, but the docs do not say"
        " which code they describe — so instead of guessing, DriftGuard is asking.",
        "",
        "Reply on this PR with the question label, one answer per line, e.g."
        " `Q1: yes, that section is about deliver()`.",
        "",
    ]
    for pending, fact_line in zip(questions, fact_lines, strict=True):
        lines.append(
            f"**{pending.label}** — `{pending.chunk.doc_path}` › *{pending.chunk.heading_path}*"
        )
        lines.append(f"> {pending.question}")
        lines.append(f"> _(the change: {fact_line})_")
        lines.append("")
    lines.append(QUESTION_MARKER)
    return "\n".join(lines)


def parse_answer_lines(body: str) -> dict[str, str]:
    """{'Q1': 'yes, that is deliver()'} out of a free-form comment body.
    Later duplicates of the same label win — people correct themselves."""
    return {f"Q{number}": text.strip() for number, text in ANSWER_LINE_RE.findall(body)}
