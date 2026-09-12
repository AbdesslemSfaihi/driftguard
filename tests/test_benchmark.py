"""The benchmark run IS a test: deterministic guarantees are gated here in CI.

Uses HashEmbedder (offline). Retrieval recall with the hash embedder is a
FLOOR, not the real number — BM25 does the heavy lifting on identifier
queries; real semantic numbers come from `evaluate --embedder ollama`.
"""

from pathlib import Path

from driftguard.evaluation.harness import run_eval
from driftguard.indexing.embedder import HashEmbedder

BENCHMARK_DIR = Path(__file__).parents[1] / "benchmark"


def test_benchmark_deterministic_guarantees_hold():
    report = run_eval(BENCHMARK_DIR, HashEmbedder(), k=5)

    assert len(report.cases) == 26
    assert report.fact_recall == 1.0, [
        (c.case_id, c.facts_matched, c.facts_expected) for c in report.cases if not c.facts_ok
    ]
    assert report.controls_clean
    # No case should produce facts beyond its labels (noise = future false positives).
    noisy = [(c.case_id, c.extra_facts) for c in report.cases if c.extra_facts]
    assert noisy == []


def test_benchmark_retrieval_floor_with_fake_embedder():
    report = run_eval(BENCHMARK_DIR, HashEmbedder(), k=5)
    # Floor calibrated at introduction; a drop below means chunking, tokenizing
    # or query building regressed. Real target (>=80%) is measured with Ollama.
    assert report.retrieval_recall >= 0.75, [
        (c.case_id, c.missed_sections) for c in report.cases if c.missed_sections
    ]


def test_judgment_stage_plumbing_with_the_fake_judge():
    """Real judgment numbers need the Anthropic judge; this proves the harness
    itself — verdict collection, section matching, protected-page accounting —
    with the deterministic fake."""
    from conftest import FakeDriftModel

    report = run_eval(BENCHMARK_DIR, HashEmbedder(), k=5, model=FakeDriftModel())

    assert report.judge == "fake-drift-model"
    # The fake marks release-notes pages historical, so nothing protected is hit.
    assert report.protected_clean
    # The fake judges 'drifted' on any old-identifier hit, so its recall can
    # never exceed retrieval recall — and should catch most of it.
    assert 0 < report.judgment_recall <= report.retrieval_recall
    assert 0 < report.judgment_precision <= 1.0
