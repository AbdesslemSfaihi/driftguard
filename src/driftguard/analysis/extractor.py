"""The drift-fact extractor — the deterministic foundation of DriftGuard.

Given the OLD and NEW source of one changed Python file, produce the list of
"drift facts": objective changes that documentation could contradict. The LLM
never guesses WHAT changed — this module states it as typed facts; the LLM only
judges later what each fact MEANS for the docs.

v1 perimeter (validated with the supervisor): MODULE-LEVEL FUNCTIONS only, and
their positional-or-keyword parameters. Classes, methods, *args/**kwargs and
keyword-only parameters are deliberately out of v1 — added here later only if
the benchmark shows they matter.
"""

import ast
import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("driftguard.analysis")

DriftKind = Literal["removed", "signature_changed", "default_changed", "renamed"]


@dataclass(frozen=True)
class DriftFact:
    """One objective, verifiable change a doc page could contradict."""

    kind: DriftKind
    symbol: str  # "connect" for a function, "connect.timeout" for a parameter
    old: str | None  # old name / old default / old signature, depending on kind
    new: str | None  # new counterpart
    file: str


@dataclass(frozen=True)
class _Param:
    name: str
    default: str | None  # source text of the default ("10", "'utf-8'"), None if required


@dataclass(frozen=True)
class _Signature:
    params: tuple[_Param, ...]

    def render(self) -> str:
        parts = [p.name if p.default is None else f"{p.name}={p.default}" for p in self.params]
        return "(" + ", ".join(parts) + ")"


def _module_functions(source: str, file_path: str) -> dict[str, _Signature] | None:
    """Map {function_name: signature} for module-level (async) functions.

    Returns None when the source does not parse — the caller must then produce
    no facts at all: half-parsed input would generate false accusations, and a
    false drift report costs developer trust (precision >= 90% is a v1 metric).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        log.warning("skipping unparseable file %s: %s", file_path, exc)
        return None

    functions: dict[str, _Signature] = {}
    for node in tree.body:  # tree.body only => module level, per the v1 perimeter
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names = [arg.arg for arg in node.args.args]
            # ast aligns defaults to the END of the arg list: def f(a, b=1)
            # has defaults == [Constant(1)]. Left-pad with None to pair them up.
            defaults = [None] * (len(names) - len(node.args.defaults)) + [
                ast.unparse(default) for default in node.args.defaults
            ]
            functions[node.name] = _Signature(
                tuple(_Param(n, d) for n, d in zip(names, defaults, strict=True))
            )
    return functions


def extract_drift_facts(old_source: str, new_source: str, file_path: str) -> list[DriftFact]:
    old_functions = _module_functions(old_source, file_path)
    new_functions = _module_functions(new_source, file_path)
    if old_functions is None or new_functions is None:
        return []

    facts: list[DriftFact] = []
    for name, old_sig in old_functions.items():
        new_sig = new_functions.get(name)

        if new_sig is None:
            facts.append(DriftFact("removed", name, old=name, new=None, file=file_path))
            continue

        if len(old_sig.params) != len(new_sig.params):
            # Arity changed: positional pairing is no longer reliable, so emit
            # one function-level fact with both signatures instead of guessing
            # parameter-by-parameter stories.
            facts.append(
                DriftFact(
                    "signature_changed",
                    name,
                    old=old_sig.render(),
                    new=new_sig.render(),
                    file=file_path,
                )
            )
            continue

        # Same arity: compare parameters position by position.
        for old_param, new_param in zip(old_sig.params, new_sig.params, strict=True):
            if old_param.name != new_param.name:
                facts.append(
                    DriftFact(
                        "renamed",
                        f"{name}.{old_param.name}",
                        old=old_param.name,
                        new=new_param.name,
                        file=file_path,
                    )
                )
            if old_param.default != new_param.default:
                facts.append(
                    DriftFact(
                        "default_changed",
                        f"{name}.{new_param.name}",
                        old=old_param.default,
                        new=new_param.default,
                        file=file_path,
                    )
                )

    return facts
