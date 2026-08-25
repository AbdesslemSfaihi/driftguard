"""Turn drift facts into documentation search queries.

The key insight: the documentation contains the OLD world — old names, old
defaults. So queries lead with the old identifier (that is what drifted docs
still say), while a natural-language variant gives the dense retriever
something to work with. Deterministic on purpose: the agent may later add
LLM query expansion, and the benchmark will tell us if it actually helps.

Symbols may be dotted ("ConnectionPool.acquire.wait"); docs overwhelmingly say
"acquire(wait=True)", not the fully qualified path, so queries use the LAST
segments (the method and parameter names) with the full symbol kept as one
extra exact-match query.
"""

from driftguard.analysis.extractor import DriftFact


def queries_for_fact(fact: DriftFact) -> list[str]:
    owner, _, param = fact.symbol.rpartition(".")
    owner_short = owner.rpartition(".")[2] or fact.symbol
    symbol_short = fact.symbol.rpartition(".")[2]

    if fact.kind == "renamed":
        return _dedupe(
            [
                str(fact.old),
                f"{owner_short} {fact.old}",
                f"{fact.old} parameter option",
            ]
        )
    if fact.kind == "default_changed":
        return _dedupe(
            [
                f"{param} {fact.old}",
                f"{owner_short} {param} default",
                f"default {param} value",
            ]
        )
    if fact.kind == "constant_changed":
        return _dedupe(
            [
                str(fact.symbol),
                f"{fact.symbol} {fact.old}",
                f"{fact.symbol} default value",
            ]
        )
    if fact.kind == "removed":
        removed_short = str(fact.old).rpartition(".")[2]
        return _dedupe(
            [
                str(fact.old),
                removed_short,
                f"{removed_short} function usage",
                f"how to use {removed_short}",
            ]
        )
    # signature_changed
    return _dedupe(
        [
            symbol_short,
            f"{symbol_short} parameters arguments",
            f"{symbol_short} example",
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
