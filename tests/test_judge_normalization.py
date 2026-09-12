"""Verdict normalization: the rules code enforces on the judge's output,
whatever the prompt said and whatever the model did with it."""

from driftguard.agent.model import SectionVerdict, normalize_verdicts
from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import DocChunk

RENAME = DriftFact("renamed", "connect.timeout", "timeout", "request_timeout", "src/client.py")
DEFAULT = DriftFact("default_changed", "connect.timeout", "10", "30", "src/client.py")
FAQ = DocChunk("docs/faq.md", "FAQ > Retries", "...", "docs/faq.md::0", 0, 3)


def unsure(question="Is this about connect()?"):
    return SectionVerdict(chunk_id=FAQ.chunk_id, verdict="unsure", reason="r", question=question)


def test_unknown_chunk_ids_are_dropped():
    stray = SectionVerdict(chunk_id="docs/other.md::9", verdict="drifted", reason="r")
    assert normalize_verdicts(DEFAULT, [FAQ], [stray]) == []


def test_unsure_without_a_question_is_demoted():
    [verdict] = normalize_verdicts(DEFAULT, [FAQ], [unsure(question="  ")])
    assert verdict.verdict == "accurate"


def test_unsure_on_a_value_fact_is_kept():
    [verdict] = normalize_verdicts(DEFAULT, [FAQ], [unsure()])
    assert verdict.verdict == "unsure" and verdict.question


def test_unsure_on_an_identifier_fact_is_demoted():
    [verdict] = normalize_verdicts(RENAME, [FAQ], [unsure()])
    assert verdict.verdict == "accurate" and verdict.question == ""
