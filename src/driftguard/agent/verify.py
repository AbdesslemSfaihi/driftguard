"""Deterministic verification of proposed doc fixes — the LLM writes, code checks.

An LLM cannot be its own referee: asking a second model call "is this fix
correct?" just moves the hallucination one step. But every v1 drift kind has a
mechanically checkable contract on the rewritten text (the old name must be
gone, the new default must appear, ...). Verification is therefore plain code:
free, instant, and impossible to sweet-talk. A failed check becomes feedback
for one bounded retry; a fix that never passes is dropped, not published —
precision over recall, always.
"""

import re

from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import HEADING_RE


def verify_fix(facts: list[DriftFact], old_text: str, new_text: str) -> str | None:
    """Return None when the rewrite passes every check, else the failure reasons
    (used verbatim as retry feedback for the fixer)."""
    if not new_text.strip():
        return "the rewritten section is empty"

    problems: list[str] = []
    if new_text.strip() == old_text.strip():
        problems.append("nothing was changed")

    old_first = old_text.splitlines()[0] if old_text.splitlines() else ""
    heading = HEADING_RE.match(old_first)
    if heading:
        new_first = new_text.splitlines()[0] if new_text.splitlines() else ""
        new_heading = HEADING_RE.match(new_first)
        if not new_heading or len(new_heading.group(1)) != len(heading.group(1)):
            problems.append(
                f"the section must start with a level-{len(heading.group(1))} heading"
            )

    for fact in facts:
        problems.extend(_check_fact(fact, new_text))
    return None if not problems else "; ".join(problems)


def _check_fact(fact: DriftFact, text: str) -> list[str]:
    problems: list[str] = []
    if fact.kind == "renamed":
        if _mentions(text, fact.old):
            problems.append(f"old parameter name `{fact.old}` still appears")
        if not _mentions(text, fact.new):
            problems.append(f"new parameter name `{fact.new}` never appears")
    elif fact.kind == "default_changed":
        if _mentions(text, fact.old):
            problems.append(f"old default value `{fact.old}` still appears")
        if not _mentions(text, fact.new):
            problems.append(f"new default value `{fact.new}` never appears")
    elif fact.kind == "removed":
        if _mentions(text, fact.old):
            problems.append(f"removed function `{fact.old}` is still mentioned")
    else:  # signature_changed — compare which parameter names each side shows
        old_params = _signature_params(fact.old)
        new_params = _signature_params(fact.new)
        for param in sorted(old_params - new_params):
            if re.search(rf"\b{re.escape(param)}\s*=", text):
                problems.append(f"removed parameter `{param}` is still shown being passed")
        for param in sorted(new_params - old_params):
            if not _mentions(text, param):
                problems.append(f"new parameter `{param}` never appears")
    return problems


def _mentions(text: str, value: str | None) -> bool:
    """Word-boundary containment. `timeout` does NOT match inside
    `request_timeout` (underscore is a word character), which is exactly the
    distinction rename checks need. Quotes around string defaults are stripped
    so `'utf-8'` matches however the docs quote it."""
    if value is None:
        return False
    needle = value.strip("\"'")
    if not needle:
        return False
    return re.search(rf"\b{re.escape(needle)}\b", text) is not None


def _signature_params(rendered: str | None) -> set[str]:
    """Parameter names out of a rendered signature like '(host, timeout=10)'."""
    if not rendered:
        return set()
    inner = rendered.strip().removeprefix("(").removesuffix(")")
    return {
        part.split("=", 1)[0].strip()
        for part in inner.split(",")
        if part.strip()
    }
