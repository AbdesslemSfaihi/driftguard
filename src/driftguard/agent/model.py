"""The LLM seam: judge + fixer behind one narrow protocol.

Same dependency-inversion move as Embedder: the graph depends on DriftModel,
not on Anthropic. Tests inject a deterministic fake (offline, instant);
production injects AnthropicModel. The two LLM jobs are deliberately tiny —
"map this fact onto these sections" and "rewrite this one section" — because
small, structured jobs are the ones a model does reliably and a harness can
measure.

Both calls use structured outputs (client.messages.parse + Pydantic): the
schema is enforced at the API layer, so there is no fragile "please answer in
JSON" parsing on our side.
"""

import logging
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel

from driftguard.agent.prompts import (
    ANSWER_SYSTEM,
    FIXER_SYSTEM,
    JUDGE_SYSTEM,
    render_answer_request,
    render_fix_request,
    render_judge_request,
)
from driftguard.analysis.extractor import DriftFact
from driftguard.indexing.chunker import DocChunk

log = logging.getLogger("driftguard.agent")


class AgentError(RuntimeError):
    """A model call failed in a way that ends this delivery's agent run."""


class SectionVerdict(BaseModel):
    chunk_id: str
    verdict: Literal["drifted", "accurate", "historical", "unsure"]
    reason: str
    # Required when verdict == "unsure": the one question a maintainer can
    # answer that decides drifted-vs-accurate. Enforced in judge(), not by the
    # schema — an unsure without a question is demoted, never guessed at.
    question: str = ""


class AnswerResolution(BaseModel):
    resolution: Literal["confirms_drift", "confirms_accurate", "unclear"]
    summary: str


class JudgeResponse(BaseModel):
    verdicts: list[SectionVerdict]


class ProposedFix(BaseModel):
    new_text: str


# Only a changed VALUE can leave a section undecidable: "3 attempts by default"
# may describe another function with the same default. A rename, removal or
# signature change is contradicted only by the old identifier shown as current
# API — a section that shows it is decidable, and one that does not is simply
# accurate. So a question about an identifier fact is never warranted.
VALUE_FACT_KINDS = frozenset({"default_changed", "constant_changed"})


def normalize_verdicts(
    fact: DriftFact, chunks: list[DocChunk], verdicts: list[SectionVerdict]
) -> list[SectionVerdict]:
    """Trust but verify the model's echo. Rules that code can enforce are not
    left to the prompt (precision first, every time):

    - verdicts for unknown chunk_ids are dropped; sections the model skipped
      default to "no action";
    - an "unsure" without a question is a hedge, not an escalation — demoted;
    - an "unsure" about a rename, removal or signature change is demoted: the
      prompt says so too, but a model that ignores the rule would interrupt a
      maintainer for nothing (measured: two needless questions per run).
    """
    known = {chunk.chunk_id for chunk in chunks}
    kept = [v for v in verdicts if v.chunk_id in known]
    missing = known - {v.chunk_id for v in kept}
    if missing:
        log.warning("judge returned no verdict for %s — treated as accurate", sorted(missing))
    for verdict in kept:
        if verdict.verdict != "unsure":
            continue
        if not verdict.question.strip():
            log.warning("unsure verdict for %s has no question — demoted", verdict.chunk_id)
            verdict.verdict = "accurate"
        elif fact.kind not in VALUE_FACT_KINDS:
            log.info(
                "unsure verdict for %s on a %s fact — only values can be ambiguous, demoted",
                verdict.chunk_id,
                fact.kind,
            )
            verdict.verdict = "accurate"
            verdict.question = ""
    return kept


class DriftModel(Protocol):
    model_name: str

    def judge(self, fact: DriftFact, chunks: list[DocChunk]) -> list[SectionVerdict]: ...

    def propose_fix(
        self,
        chunk: DocChunk,
        facts: list[DriftFact],
        feedback: str | None = None,
        developer_note: str | None = None,
    ) -> str: ...

    def interpret_answer(self, question: str, fact: DriftFact, answer: str) -> AnswerResolution: ...


class AnthropicModel:
    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        self.model_name = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def judge(self, fact: DriftFact, chunks: list[DocChunk]) -> list[SectionVerdict]:
        response = self._client.messages.parse(
            model=self.model_name,
            max_tokens=16000,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": render_judge_request(fact, chunks)}],
            output_format=JudgeResponse,
        )
        self._check_stop(response)
        return normalize_verdicts(fact, chunks, response.parsed_output.verdicts)

    def propose_fix(
        self,
        chunk: DocChunk,
        facts: list[DriftFact],
        feedback: str | None = None,
        developer_note: str | None = None,
    ) -> str:
        response = self._client.messages.parse(
            model=self.model_name,
            max_tokens=16000,
            system=FIXER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": render_fix_request(chunk, facts, feedback, developer_note),
                }
            ],
            output_format=ProposedFix,
        )
        self._check_stop(response)
        return response.parsed_output.new_text

    def interpret_answer(self, question: str, fact: DriftFact, answer: str) -> AnswerResolution:
        response = self._client.messages.parse(
            model=self.model_name,
            max_tokens=2000,
            system=ANSWER_SYSTEM,
            messages=[{"role": "user", "content": render_answer_request(question, fact, answer)}],
            output_format=AnswerResolution,
        )
        self._check_stop(response)
        return response.parsed_output

    @staticmethod
    def _check_stop(response: object) -> None:
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            explanation = getattr(details, "explanation", None) or "no details"
            raise AgentError(f"model refused the request: {explanation}")
        if stop_reason == "max_tokens":
            raise AgentError("model response was truncated (hit max_tokens)")
