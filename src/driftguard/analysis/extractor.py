"""EXERCISE 01 — the drift-fact extractor. This one is yours, Abdesslem.

Goal
----
Given the OLD and NEW source of one changed Python file, produce the list of
"drift facts" — the objective changes that documentation could contradict.
This is the deterministic foundation the whole agent reasons over: the LLM
never guesses WHAT changed, it only judges what the change MEANS for the docs.

Contract (the tests in tests/test_extractor.py are the authority)
-----------------------------------------------------------------
Detect, for MODULE-LEVEL FUNCTIONS only (v1 perimeter):
  1. "removed"            a function present in old, absent in new
  2. "signature_changed"  a parameter added or removed on an existing function
  3. "default_changed"    same parameter, different default value
  4. "renamed"            a parameter at the same position whose name changed
                          (arity unchanged); if its default ALSO changed, emit
                          BOTH a renamed fact and a default_changed fact

Hints
-----
- Use the standard library `ast` module: ast.parse(source), then walk the tree
  for ast.FunctionDef nodes. You need names, node.args.args, node.args.defaults.
- Defaults align to the END of the argument list: def f(a, b=1) has defaults
  only for b. Pair them up carefully (ast.unparse turns a default node back
  into a string like "10").
- Build a dict {function_name: signature_info} for old and new, then compare.
  Comparing two dicts is much easier than comparing two trees.
- Start with the "removed" test, make it pass, move to the next. One test at
  a time — do not try to make everything pass at once.

To start: delete the `pytestmark = pytest.mark.skip(...)` line at the top of
tests/test_extractor.py and run `pytest tests/test_extractor.py -x`.
"""

from dataclasses import dataclass
from typing import Literal

DriftKind = Literal["removed", "signature_changed", "default_changed", "renamed"]


@dataclass(frozen=True)
class DriftFact:
    """One objective, verifiable change a doc page could contradict."""

    kind: DriftKind
    symbol: str  # e.g. "connect" or "connect.timeout" for a parameter
    old: str | None  # old name or old default, depending on kind
    new: str | None  # new name or new default, depending on kind
    file: str


def extract_drift_facts(old_source: str, new_source: str, file_path: str) -> list[DriftFact]:
    raise NotImplementedError("Exercise 01 — see the module docstring and tests/test_extractor.py")
