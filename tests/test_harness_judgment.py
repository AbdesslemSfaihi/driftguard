"""The harness must resolve verdicts the way the agent does.

The graph's judge node lets a "drifted" verdict from any fact settle a section
and drops questions other facts raised about it. The scorecard has to apply
the same rule, or it would charge the judge with a needless question the agent
never asks — a measuring stick that disagrees with the thing it measures.
"""

from driftguard.agent.model import SectionVerdict
from driftguard.analysis.extractor import DriftFact
from driftguard.evaluation.harness import CaseResult, _judge_case
from driftguard.indexing.chunker import DocChunk

RENAME = DriftFact("renamed", "connect.timeout", "timeout", "request_timeout", "src/client.py")
DEFAULT = DriftFact("default_changed", "connect.timeout", "10", "30", "src/client.py")

CONFIG = DocChunk("docs/config.md", "Configuration > Timeouts", "...", "docs/config.md::0", 0, 3)
FAQ = DocChunk("docs/faq.md", "FAQ > How many retries?", "...", "docs/faq.md::0", 0, 3)

META = {
    "affected": [{"doc": "docs/config.md", "heading": "Timeouts"}],
    "ambiguous": [{"doc": "docs/faq.md", "heading": "How many retries"}],
    "protected": ["docs/release-notes-v1.md"],
}


class ScriptedModel:
    """Verdicts looked up by (fact kind, chunk id); anything unlisted is accurate."""

    model_name = "scripted"

    def __init__(self, script: dict[tuple[str, str], tuple[str, str]]):
        self.script = script

    def judge(self, fact, chunks):
        verdicts = []
        for chunk in chunks:
            verdict, question = self.script.get((fact.kind, chunk.chunk_id), ("accurate", ""))
            verdicts.append(
                SectionVerdict(
                    chunk_id=chunk.chunk_id, verdict=verdict, reason="scripted", question=question
                )
            )
        return verdicts


def fresh_result() -> CaseResult:
    return CaseResult(
        case_id="t",
        facts_expected=2,
        facts_matched=2,
        extra_facts=[],
        sections_expected=1,
        sections_hit=1,
        missed_sections=[],
        control=False,
        control_clean=True,
    )


def test_a_drifted_verdict_settles_a_section_whatever_the_fact_order():
    script = {
        ("renamed", CONFIG.chunk_id): ("unsure", "Is this connect()?"),
        ("default_changed", CONFIG.chunk_id): ("drifted", ""),
    }
    forward = [(RENAME, [CONFIG]), (DEFAULT, [CONFIG])]
    for evidence in (forward, forward[::-1]):
        result = fresh_result()
        _judge_case(result, META, evidence, ScriptedModel(script))
        assert result.judged_sections_hit == 1 and not result.judged_missed
        assert not result.false_asks and not result.false_alarms


def test_asking_is_right_on_ambiguous_sections_and_wrong_elsewhere():
    script = {
        ("default_changed", FAQ.chunk_id): ("unsure", "Which function is this about?"),
        ("default_changed", CONFIG.chunk_id): ("unsure", "Really?"),
    }
    result = fresh_result()
    _judge_case(result, META, [(DEFAULT, [CONFIG, FAQ])], ScriptedModel(script))
    assert result.asks_expected == 1 and result.asks_hit == 1 and not result.asks_missed
    assert result.false_asks == ["docs/config.md :: Configuration > Timeouts"]
    assert result.judged_missed == ["docs/config.md :: Timeouts"]


def test_claiming_certainty_on_an_ambiguous_section_is_a_false_alarm():
    script = {("default_changed", FAQ.chunk_id): ("drifted", "")}
    result = fresh_result()
    _judge_case(result, META, [(DEFAULT, [FAQ])], ScriptedModel(script))
    assert result.false_alarms == ["docs/faq.md :: FAQ > How many retries?"]
    assert result.asks_hit == 0 and result.asks_missed == ["docs/faq.md :: How many retries"]
