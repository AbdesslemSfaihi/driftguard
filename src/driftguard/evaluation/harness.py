"""The planted-drift benchmark harness — DriftGuard's measuring stick.

Each case under benchmark/cases/ plants one known change (fixture file ->
new.py) and labels which doc sections become wrong. The harness measures the
pipeline stage by stage:

  1. FACT RECALL      did extract_drift_facts() find every planted fact?
                      (deterministic — the gate is 100%, anything less is a bug)
  2. RETRIEVAL RECALL for each affected doc section, did the hybrid index
                      surface it in the top-k of at least one fact query?
                      (the ceiling for the whole system: what retrieval
                      misses, the judge can never fix)
  3. JUDGMENT         (optional, needs a DriftModel) of the retrieved sections,
                      which did the judge mark drifted? Measured as recall
                      (labeled sections caught), precision (drifted verdicts
                      that are labeled), and PROTECTED pages — release notes
                      the judge must classify historical, never drifted.

Control cases invert the game: zero facts must be produced.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from driftguard.analysis.extractor import DriftFact, extract_drift_facts
from driftguard.analysis.queries import queries_for_fact
from driftguard.indexing.chunker import DocChunk
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
    # Judgment stage (populated only when a model is passed)
    judged_sections_hit: int = 0
    judged_missed: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)
    protected_violations: list[str] = field(default_factory=list)

    @property
    def facts_ok(self) -> bool:
        return self.facts_matched == self.facts_expected


@dataclass
class EvalReport:
    embedder: str
    k: int
    judge: str | None = None
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
    def judgment_recall(self) -> float:
        """Of the labeled affected sections, how many did the judge catch?"""
        expected = sum(c.sections_expected for c in self.cases)
        hit = sum(c.judged_sections_hit for c in self.cases)
        return hit / expected if expected else 1.0

    @property
    def judgment_precision(self) -> float:
        """Of the sections the judge called drifted, how many are labeled?
        This is the trust metric: every false alarm is a wrong accusation."""
        hits = sum(c.judged_sections_hit for c in self.cases)
        alarms = sum(len(c.false_alarms) for c in self.cases)
        return hits / (hits + alarms) if (hits + alarms) else 1.0

    @property
    def protected_clean(self) -> bool:
        return all(not c.protected_violations for c in self.cases)

    @property
    def controls_clean(self) -> bool:
        return all(c.control_clean for c in self.cases if c.control)

    @property
    def deterministic_ok(self) -> bool:
        """The parts with no model in them must be perfect."""
        return self.fact_recall == 1.0 and self.controls_clean

    def to_dict(self) -> dict:
        report = {
            "embedder": self.embedder,
            "k": self.k,
            "fact_recall": round(self.fact_recall, 4),
            "retrieval_recall": round(self.retrieval_recall, 4),
            "controls_clean": self.controls_clean,
            "cases": [vars(c) for c in self.cases],
        }
        if self.judge:
            report["judge"] = self.judge
            report["judgment_recall"] = round(self.judgment_recall, 4)
            report["judgment_precision"] = round(self.judgment_precision, 4)
            report["protected_clean"] = self.protected_clean
        return report


def run_eval(benchmark_dir: Path, embedder: Embedder, k: int = 5, model=None) -> EvalReport:
    fixture_dir = benchmark_dir / "fixture"
    index = HybridIndex(embedder)
    index.build(load_corpus(fixture_dir))

    report = EvalReport(embedder=embedder.model_name, k=k, judge=getattr(model, "model_name", None))
    for case_dir in sorted((benchmark_dir / "cases").iterdir()):
        if not (case_dir / "case.json").exists():
            continue
        report.cases.append(_run_case(case_dir, fixture_dir, index, k, model))
    return report


def _run_case(
    case_dir: Path, fixture_dir: Path, index: HybridIndex, k: int, model
) -> CaseResult:
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    old_source = (fixture_dir / meta["file"]).read_text(encoding="utf-8")
    new_source = (case_dir / "new.py").read_text(encoding="utf-8")

    facts = extract_drift_facts(old_source, new_source, meta["file"])
    found = {(f.kind, f.symbol) for f in facts}
    expected = {(f["kind"], f["symbol"]) for f in meta["expected_facts"]}
    extra = sorted(f"{kind}:{symbol}" for kind, symbol in found - expected)

    # Retrieval: per-fact top-k evidence (deduplicated), exactly what the
    # agent's search node hands the judge.
    evidence: list[tuple[DriftFact, list[DocChunk]]] = []
    for fact in facts:
        seen: set[str] = set()
        chunks: list[DocChunk] = []
        for query in queries_for_fact(fact):
            for result in index.search(query, k=k):
                if result.chunk.chunk_id not in seen:
                    seen.add(result.chunk.chunk_id)
                    chunks.append(result.chunk)
        evidence.append((fact, chunks))
    retrieved = [chunk for _, chunks in evidence for chunk in chunks]

    missed: list[str] = []
    hits = 0
    for section in meta["affected"]:
        if any(_matches(chunk, section) for chunk in retrieved):
            hits += 1
        else:
            missed.append(f"{section['doc']} :: {section.get('heading', '*')}")

    result = CaseResult(
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
    if model is not None:
        _judge_case(result, meta, evidence, model)
    return result


def _judge_case(result: CaseResult, meta: dict, evidence: list, model) -> None:
    protected = set(meta.get("protected", []))
    drifted: list[DocChunk] = []
    seen: set[str] = set()
    for fact, chunks in evidence:
        if not chunks:
            continue
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        for verdict in model.judge(fact, chunks):
            if verdict.verdict == "drifted" and verdict.chunk_id not in seen:
                seen.add(verdict.chunk_id)
                drifted.append(by_id[verdict.chunk_id])

    for section in meta["affected"]:
        if any(_matches(chunk, section) for chunk in drifted):
            result.judged_sections_hit += 1
        else:
            result.judged_missed.append(f"{section['doc']} :: {section.get('heading', '*')}")

    for chunk in drifted:
        if chunk.doc_path in protected:
            result.protected_violations.append(f"{chunk.doc_path} :: {chunk.heading_path}")
        elif not any(_matches(chunk, section) for section in meta["affected"]):
            result.false_alarms.append(f"{chunk.doc_path} :: {chunk.heading_path}")


def _matches(chunk: DocChunk, section: dict) -> bool:
    return (
        chunk.doc_path == section["doc"]
        and section.get("heading", "").lower() in chunk.heading_path.lower()
    )
