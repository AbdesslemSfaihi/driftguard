"""The planted-drift benchmark harness — DriftGuard's measuring stick.

Each case under benchmark/cases/ plants one known change (fixture file ->
new.py) and labels which doc sections become wrong. The harness measures the
two stages that exist today:

  1. FACT RECALL      did extract_drift_facts() find every planted fact?
                      (deterministic — the gate is 100%, anything less is a bug)
  2. RETRIEVAL RECALL for each affected doc section, did the hybrid index
                      surface it in the top-k of at least one fact query?
                      (the ceiling for the whole system: what retrieval
                      misses, the judge can never fix)

Control cases invert the game: zero facts must be produced. Week 2 extends
this same harness with the judgment stage, adding end-to-end precision.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from driftguard.analysis.extractor import extract_drift_facts
from driftguard.analysis.queries import queries_for_fact
from driftguard.indexing.corpus import load_corpus
from driftguard.indexing.embedder import Embedder
from driftguard.indexing.index import HybridIndex


@dataclass
class CaseResult:
    case_id: str
    facts_expected: int
    facts_matched: int
    extra_facts: list[str]
    sections_expected: int
    sections_hit: int
    missed_sections: list[str]
    control: bool
    control_clean: bool  # for controls: True when zero facts were produced

    @property
    def facts_ok(self) -> bool:
        return self.facts_matched == self.facts_expected


@dataclass
class EvalReport:
    embedder: str
    k: int
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def fact_recall(self) -> float:
        expected = sum(c.facts_expected for c in self.cases)
        matched = sum(c.facts_matched for c in self.cases)
        return matched / expected if expected else 1.0

    @property
    def retrieval_recall(self) -> float:
        expected = sum(c.sections_expected for c in self.cases)
        hit = sum(c.sections_hit for c in self.cases)
        return hit / expected if expected else 1.0

    @property
    def controls_clean(self) -> bool:
        return all(c.control_clean for c in self.cases if c.control)

    @property
    def deterministic_ok(self) -> bool:
        """The parts with no model in them must be perfect."""
        return self.fact_recall == 1.0 and self.controls_clean

    def to_dict(self) -> dict:
        return {
            "embedder": self.embedder,
            "k": self.k,
            "fact_recall": round(self.fact_recall, 4),
            "retrieval_recall": round(self.retrieval_recall, 4),
            "controls_clean": self.controls_clean,
            "cases": [vars(c) for c in self.cases],
        }


def run_eval(benchmark_dir: Path, embedder: Embedder, k: int = 5) -> EvalReport:
    fixture_dir = benchmark_dir / "fixture"
    index = HybridIndex(embedder)
    index.build(load_corpus(fixture_dir))

    report = EvalReport(embedder=embedder.model_name, k=k)
    for case_dir in sorted((benchmark_dir / "cases").iterdir()):
        if not (case_dir / "case.json").exists():
            continue
        report.cases.append(_run_case(case_dir, fixture_dir, index, k))
    return report


def _run_case(case_dir: Path, fixture_dir: Path, index: HybridIndex, k: int) -> CaseResult:
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    old_source = (fixture_dir / meta["file"]).read_text(encoding="utf-8")
    new_source = (case_dir / "new.py").read_text(encoding="utf-8")

    facts = extract_drift_facts(old_source, new_source, meta["file"])
    found = {(f.kind, f.symbol) for f in facts}
    expected = {(f["kind"], f["symbol"]) for f in meta["expected_facts"]}
    extra = sorted(f"{kind}:{symbol}" for kind, symbol in found - expected)

    # Retrieval: union of top-k hits across every query of every fact.
    hit_chunks = []
    seen_ids: set[str] = set()
    for fact in facts:
        for query in queries_for_fact(fact):
            for result in index.search(query, k=k):
                if result.chunk.chunk_id not in seen_ids:
                    seen_ids.add(result.chunk.chunk_id)
                    hit_chunks.append(result.chunk)

    missed: list[str] = []
    hits = 0
    for section in meta["affected"]:
        matched = any(
            chunk.doc_path == section["doc"]
            and section.get("heading", "").lower() in chunk.heading_path.lower()
            for chunk in hit_chunks
        )
        if matched:
            hits += 1
        else:
            missed.append(f"{section['doc']} :: {section.get('heading', '*')}")

    return CaseResult(
        case_id=meta["id"],
        facts_expected=len(expected),
        facts_matched=len(expected & found),
        extra_facts=extra,
        sections_expected=len(meta["affected"]),
        sections_hit=hits,
        missed_sections=missed,
        control=bool(meta.get("control")),
        control_clean=not meta.get("control") or not facts,
    )
