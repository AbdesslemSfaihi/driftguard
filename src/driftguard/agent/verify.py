"""Deterministic verification of proposed doc fixes — the LLM writes, code checks.

An LLM cannot be its own referee: asking a second model call "is this fix
correct?" just moves the hallucination one step. But every v1 drift kind has a
mechanically checkable contract on the rewritten text (the old name must be
gone, the new default must appear, ...). Verification is therefore plain code:
free, instant, and impossible to sweet-talk. A failed check becomes feedback
for one bounded retry; a fix that never passes is dropped, not published —
precision over recall, always.

The old-identifier checks are CONTEXT-AWARE: `timeout` the parameter must
disappear, but "raising a timeout error" is legitimate English and must not
fail a fix. The first live run proved this matters — a correct api-reference
rewrite was rejected because the strict check could not tell the two apart.
An occurrence counts as the identifier only inside code fences, backticks,
emphasis markers, or when followed by '=' or '('.
"""

import re

from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import FENCE_RE, HEADING_RE


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
        if _mentions_identifier(text, fact.old):
            problems.append(f"old parameter name `{fact.old}` still appears as an identifier")
        if not _mentions(text, fact.new):
            problems.append(f"new parameter name `{fact.new}` never appears")
    elif fact.kind == "default_changed":
        # Values stay strict on purpose: a lingering "10 seconds" in prose IS
        # usually the stale default, unlike a name which doubles as English.
        if _mentions(text, fact.old):
            problems.append(f"old default value `{fact.old}` still appears")
        if not _mentions(text, fact.new):
            problems.append(f"new default value `{fact.new}` never appears")
    elif fact.kind == "constant_changed":
        if _mentions(text, fact.old):
            problems.append(f"old value `{fact.old}` of `{fact.symbol}` still appears")
        if not _mentions(text, fact.new):
            problems.append(f"new value `{fact.new}` of `{fact.symbol}` never appears")
    elif fact.kind == "removed":
        # Docs say "drain()", not "ConnectionPool.drain" — check the last segment.
        removed_short = str(fact.old).rpartition(".")[2]
        if _mentions_identifier(text, removed_short):
            problems.append(f"removed `{fact.old}` is still shown as available API")
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


def _mentions_identifier(text: str, name: str | None) -> bool:
    """True when *name* appears AS A CODE IDENTIFIER: inside a fenced code
    block, touching backticks or emphasis markers, or followed by '=' or '('.
    Plain-English use of the same word is not a contradiction — "raising a
    timeout error" is fine after `timeout` was renamed. Used only for
    must-be-gone checks; presence checks stay loose (any mention is evidence)."""
    if not name:
        return False
    needle = name.strip("\"'")
    if not needle:
        return False
    pattern = re.compile(rf"\b{re.escape(needle)}\b")
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        for match in pattern.finditer(line):
            if in_fence:
                return True
            before = line[match.start() - 1] if match.start() > 0 else " "
            after = line[match.end() :].lstrip() or " "
            if before in "`*" or line[match.end() : match.end() + 1] in ("`", "*"):
                return True
            if after[0] in "=(":
                return True
    return False


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
