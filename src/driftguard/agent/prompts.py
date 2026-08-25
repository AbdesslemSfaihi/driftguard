"""Prompt construction for the judge and the fixer.

Isolated from the transport (model.py) so prompts can be read, reviewed, and
tested as plain functions — prompt text is product logic, not plumbing.

Design rules baked into both prompts:
- The model never decides WHAT changed: facts arrive as ground truth from the
  AST extractor. The judge only maps facts onto doc sections; the fixer only
  rewrites text. Smallest possible job per call = fewest hallucination paths.
- The judge must classify "historical" pages (release notes, changelogs):
  they describe the old world ON PURPOSE and are protected — a benchmark
  metric, not a nice-to-have.
"""

from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import DocChunk

JUDGE_SYSTEM = """\
You are DriftGuard's documentation judge. You receive one DRIFT FACT — an
objective code change extracted from a merged pull request by AST comparison
(it is verified ground truth; never second-guess it) — and several candidate
documentation sections that a search engine flagged as possibly related.

For EACH section, decide what the fact means for it:

- "drifted": the section asserts or shows something the fact makes incorrect —
  the old name presented as current, the old default value, a removed function
  presented as available API, an outdated signature in prose or code examples.
- "accurate": the section is not contradicted. Merely mentioning the changed
  symbol is NOT enough — the section must state something that is now false.
- "historical": the section intentionally describes the past — release notes,
  changelogs, migration guides, "in version X" narratives. Such sections are
  correct BECAUSE they describe old behavior and must never be edited.

Be precise, not eager: a wrong "drifted" verdict costs developer trust.
Two precision rules, added after measured judgment failures:
- Parameters belong to functions. A fact about one function's parameter says
  nothing about a DIFFERENT function whose parameter happens to share the
  name. Before "drifted", confirm the section refers to the changed
  function's own parameter.
- A rename changes an identifier, not the English language. Prose using the
  old word in its ordinary sense ("the host name", "three attempts"), or code
  passing the value positionally without naming the parameter, is not
  contradicted. Only sections that present the old identifier as current API
  are drifted.
Return exactly one verdict per section, echoing its chunk_id verbatim, with a
one-sentence reason quoting or pointing at the contradicting statement.
"""

FIXER_SYSTEM = """\
You are DriftGuard's documentation fixer. You receive one markdown section and
the verified drift facts that make it incorrect. Rewrite the section so it
matches the new truth of the code.

Rules:
- Minimal edits: change only what the facts make wrong — names, values,
  signatures, and the sentences or code lines describing them. Keep every
  other word, line, and formatting choice exactly as it was.
- Preserve the structure: same heading line at the same level, same lists,
  tables, and code fences.
- The docs must simply be correct: never mention the old name or value as
  "formerly ...", and never add notes about the change itself.
- Return only the rewritten markdown for this section, nothing else.
"""


def describe_fact(fact: DriftFact) -> str:
    owner, _, param = fact.symbol.rpartition(".")
    if fact.kind == "renamed":
        return (
            f"Parameter `{fact.old}` of `{owner}()` was RENAMED"
            f" to `{fact.new}` (in {fact.file})."
        )
    if fact.kind == "default_changed":
        return (
            f"The default value of parameter `{param}` of `{owner}()`"
            f" CHANGED from `{fact.old}` to `{fact.new}` (in {fact.file})."
        )
    if fact.kind == "constant_changed":
        return (
            f"The module constant `{fact.symbol}` CHANGED from `{fact.old}`"
            f" to `{fact.new}` (in {fact.file})."
        )
    if fact.kind == "removed":
        return f"`{fact.old}` was REMOVED — it no longer exists (in {fact.file})."
    return (
        f"The signature of `{fact.symbol}()` CHANGED from"
        f" `{fact.symbol}{fact.old}` to `{fact.symbol}{fact.new}` (in {fact.file})."
    )


def render_judge_request(fact: DriftFact, chunks: list[DocChunk]) -> str:
    sections = "\n\n".join(
        f'<section chunk_id="{chunk.chunk_id}" doc="{chunk.doc_path}"'
        f' heading="{chunk.heading_path}">\n{chunk.text}\n</section>'
        for chunk in chunks
    )
    return f"DRIFT FACT:\n{describe_fact(fact)}\n\nCANDIDATE SECTIONS:\n\n{sections}"


def render_fix_request(chunk: DocChunk, facts: list[DriftFact], feedback: str | None) -> str:
    fact_lines = "\n".join(f"- {describe_fact(fact)}" for fact in facts)
    retry = (
        ""
        if not feedback
        else (
            "\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED by automatic verification:"
            f"\n{feedback}\nFix those problems this time.\n"
        )
    )
    return (
        f"DRIFT FACTS (verified ground truth):\n{fact_lines}\n{retry}\n"
        f"SECTION TO REWRITE (from {chunk.doc_path}, under \"{chunk.heading_path}\"):\n\n"
        f"{chunk.text}"
    )
