"""Run the planted-drift benchmark and print the scorecard.

    python -m driftguard.cli.evaluate --embedder fake          # offline (CI)
    python -m driftguard.cli.evaluate --embedder ollama        # real numbers

Exit code 1 when a DETERMINISTIC guarantee is broken (fact recall < 100% or a
control case produced facts) — those are bugs, not tuning. Retrieval recall is
reported and tracked, not hard-gated: it moves with embedder quality and is
judged against the project target (>= 80% with the real embedder).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from driftguard.config import get_settings
from driftguard.evaluation.harness import run_eval
from driftguard.indexing.embedder import HashEmbedder, OllamaEmbedder


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="driftguard-evaluate", description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=Path("benchmark"))
    parser.add_argument("--embedder", choices=["fake", "ollama"], default="fake")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None, help="also write the full report here")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level="WARNING")
    embedder = (
        HashEmbedder()
        if args.embedder == "fake"
        else OllamaEmbedder(settings.ollama_base_url, settings.embedding_model)
    )

    report = run_eval(args.benchmark, embedder, k=args.k)

    for case in report.cases:
        if case.control:
            state = "CLEAN" if case.control_clean else "DIRTY"
            print(f"{case.case_id:34s} control: {state}")
            continue
        flags = "" if case.facts_ok else "  << FACTS MISSED"
        extra = f"  (+{len(case.extra_facts)} extra)" if case.extra_facts else ""
        print(
            f"{case.case_id:34s} facts {case.facts_matched}/{case.facts_expected}{extra}"
            f"   retrieval {case.sections_hit}/{case.sections_expected}{flags}"
        )
        for miss in case.missed_sections:
            print(f"{'':34s}   missed: {miss}")

    print("-" * 72)
    print(
        f"FACT RECALL      {report.fact_recall:6.1%}   (gate: 100%)\n"
        f"RETRIEVAL RECALL {report.retrieval_recall:6.1%}   @k={report.k}, "
        f"embedder={report.embedder} (target with ollama: >= 80%)\n"
        f"CONTROLS         {'clean' if report.controls_clean else 'DIRTY'}"
    )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"report written to {args.json}")

    if not report.deterministic_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
