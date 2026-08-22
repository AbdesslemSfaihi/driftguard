"""Turn drift facts into documentation search queries.

The key insight: the documentation contains the OLD world — old names, old
defaults. So queries lead with the old identifier (that is what drifted docs
still say), while a natural-language variant gives the dense retriever
something to work with. Deterministic on purpose: the agent may later add
LLM query expansion, and the benchmark will tell us if it actually helps.
"""

from driftguard.analysis.extractor import DriftFact


def queries_for_fact(fact: DriftFact) -> list[str]:
    function_name, _, param = fact.symbol.partition(".")

    if fact.kind == "renamed":
        return _dedupe(
            [
                str(fact.old),
                f"{function_name} {fact.old}",
                f"{fact.old} parameter option",
            ]
        )
    if fact.kind == "default_changed":
        return _dedupe(
            [
                f"{param} {fact.old}",
                f"{function_name} {param} default",
                f"default {param} value",
            ]
        )
    if fact.kind == "removed":
        return _dedupe(
            [
                str(fact.old),
                f"{fact.old} function usage",
                f"how to use {fact.old}",
            ]
        )
    # signature_changed
    return _dedupe(
        [
            function_name,
            f"{function_name} parameters arguments",
            f"{function_name} example",
        ]
    )


def _dedupe(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        if query and query not in seen:
            seen.add(query)
            out.append(query)
    return out
