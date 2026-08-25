"""The drift-fact extractor — the deterministic foundation of DriftGuard.

Given the OLD and NEW source of one changed Python file, produce the list of
"drift facts": objective changes that documentation could contradict. The LLM
never guesses WHAT changed — this module states it as typed facts; the LLM only
judges later what each fact MEANS for the docs.

Perimeter v1.1 (each extension was admitted by planted benchmark cases, per
the project rule — never by speculation):
- module-level functions and their positional-or-keyword parameters   (v1)
- top-level classes: the constructor (documented as ClassName(...), so
  __init__ maps to the class name) and public methods; `self`/`cls` are
  implementation detail, never documented, and are dropped from signatures
- UPPER_CASE module constants (lowercase module variables are internals)

Still out, awaiting benchmark evidence: nested classes, keyword-only
parameters, *args/**kwargs, type annotations, exceptions, function renames.
"""

import ast
import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("driftguard.analysis")

DriftKind = Literal[
    "removed", "signature_changed", "default_changed", "renamed", "constant_changed"
]


@dataclass(frozen=True)
class DriftFact:
    """One objective, verifiable change a doc page could contradict."""

    kind: DriftKind
    symbol: str  # "connect", "connect.timeout", "Pool.acquire.wait", "POOL_TIMEOUT"
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


@dataclass(frozen=True)
class _ModuleApi:
    callables: dict[str, _Signature]  # "connect", "Pool" (its constructor), "Pool.acquire"
    constants: dict[str, str]  # "POOL_TIMEOUT" -> "15" (source text of the value)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, method: bool) -> _Signature:
    names = [arg.arg for arg in node.args.args]
    # ast aligns defaults to the END of the arg list: def f(a, b=1)
    # has defaults == [Constant(1)]. Left-pad with None to pair them up.
    defaults = [None] * (len(names) - len(node.args.defaults)) + [
        ast.unparse(default) for default in node.args.defaults
    ]
    params = list(zip(names, defaults, strict=True))
    if method and params and params[0][0] in ("self", "cls"):
        params = params[1:]
    return _Signature(tuple(_Param(name, default) for name, default in params))


def _module_api(source: str, file_path: str) -> _ModuleApi | None:
    """The documentable surface of one module.

    Returns None when the source does not parse — the caller must then produce
    no facts at all: half-parsed input would generate false accusations, and a
    false drift report costs developer trust (precision >= 90% is a v1 metric).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        log.warning("skipping unparseable file %s: %s", file_path, exc)
        return None

    callables: dict[str, _Signature] = {}
    constants: dict[str, str] = {}
    for node in tree.body:  # tree.body only => module level, per the perimeter
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            callables[node.name] = _signature(node, method=False)
        elif isinstance(node, ast.ClassDef):
            # Docs describe construction as ClassName(...), so the class entry
            # carries __init__'s signature (empty when there is no __init__).
            callables[node.name] = _Signature(())
            for item in node.body:
                if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if item.name == "__init__":
                    callables[node.name] = _signature(item, method=True)
                elif not item.name.startswith("_"):  # private/dunder: not documented API
                    callables[f"{node.name}.{item.name}"] = _signature(item, method=True)
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name.isupper() and not name.startswith("_"):
                    constants[name] = ast.unparse(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                name = node.target.id
                if name.isupper() and not name.startswith("_"):
                    constants[name] = ast.unparse(node.value)
    return _ModuleApi(callables, constants)


def extract_drift_facts(old_source: str, new_source: str, file_path: str) -> list[DriftFact]:
    old_api = _module_api(old_source, file_path)
    new_api = _module_api(new_source, file_path)
    if old_api is None or new_api is None:
        return []

    facts: list[DriftFact] = []

    for name, old_value in old_api.constants.items():
        new_value = new_api.constants.get(name)
        if new_value is None:
            facts.append(DriftFact("removed", name, old=name, new=None, file=file_path))
        elif new_value != old_value:
            facts.append(
                DriftFact("constant_changed", name, old=old_value, new=new_value, file=file_path)
            )

    # A removed class removes every method with it: ONE fact for the class,
    # not one per member — the docs fix is "this class is gone", said once.
    removed_owners = {
        name
        for name in old_api.callables
        if "." not in name and name not in new_api.callables
    }
    for name, old_sig in old_api.callables.items():
        new_sig = new_api.callables.get(name)

        if new_sig is None:
            if "." in name and name.partition(".")[0] in removed_owners:
                continue
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

        # Same arity: positional pairing lies when parameters were REORDERED —
        # f(a, b) -> f(b, a) is not two renames. A true rename means the old
        # name vanished and the new name is genuinely new; if either name still
        # exists elsewhere in the other signature, emit one honest function-
        # level fact instead of false per-parameter stories (precision first).
        old_names = {p.name for p in old_sig.params}
        new_names = {p.name for p in new_sig.params}
        reordered = any(
            o.name != n.name and (o.name in new_names or n.name in old_names)
            for o, n in zip(old_sig.params, new_sig.params, strict=True)
        )
        if reordered:
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

        # Positional pairing is now trustworthy: compare name and default.
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
