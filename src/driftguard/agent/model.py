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
    FIXER_SYSTEM,
    JUDGE_SYSTEM,
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
    verdict: Literal["drifted", "accurate", "historical"]
    reason: str


class JudgeResponse(BaseModel):
    verdicts: list[SectionVerdict]


class ProposedFix(BaseModel):
    new_text: str


class DriftModel(Protocol):
    model_name: str

    def judge(self, fact: DriftFact, chunks: list[DocChunk]) -> list[SectionVerdict]: ...

    def propose_fix(
        self, chunk: DocChunk, facts: list[DriftFact], feedback: str | None = None
    ) -> str: ...


class AnthropicModel:
    def __init__(self, api_key: str, model: str = "claude-opus-5"):
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
        # Trust but verify the echo: verdicts for unknown chunk_ids are dropped,
        # sections the model skipped default to "no action" (precision first).
        known = {chunk.chunk_id for chunk in chunks}
        verdicts = [v for v in response.parsed_output.verdicts if v.chunk_id in known]
        missing = known - {v.chunk_id for v in verdicts}
        if missing:
            log.warning("judge returned no verdict for %s — treated as accurate", sorted(missing))
        return verdicts

    def propose_fix(
        self, chunk: DocChunk, facts: list[DriftFact], feedback: str | None = None
    ) -> str:
        response = self._client.messages.parse(
            model=self.model_name,
            max_tokens=16000,
            system=FIXER_SYSTEM,
            messages=[{"role": "user", "content": render_fix_request(chunk, facts, feedback)}],
            output_format=ProposedFix,
        )
        self._check_stop(response)
        return response.parsed_output.new_text

    @staticmethod
    def _check_stop(response: object) -> None:
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            explanation = getattr(details, "explanation", None) or "no details"
            raise AgentError(f"model refused the request: {explanation}")
        if stop_reason == "max_tokens":
            raise AgentError("model response was truncated (hit max_tokens)")
