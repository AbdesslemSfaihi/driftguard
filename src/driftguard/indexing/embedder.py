"""Embedding abstraction.

HybridIndex depends on this Protocol, not on Ollama: tests inject a
deterministic fake (offline, instant), production injects OllamaEmbedder.
Classic dependency inversion — the expensive/flaky dependency lives behind
the narrowest possible interface (one method).

OllamaEmbedder talks to Ollama's REST API directly with httpx — the same
"own the 30 lines" reasoning as the GitHub client: nothing to explain
second-hand, no framework version churn for a single POST endpoint.
"""

from typing import Protocol

import httpx

BATCH_SIZE = 64  # keep request bodies sane on large corpora


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        *,
        client: httpx.Client | None = None,
    ):
        self.model_name = model
        self._base_url = base_url
        self._http = client or httpx.Client(base_url=base_url, timeout=60.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            try:
                response = self._http.post(
                    "/api/embed", json={"model": self.model_name, "input": batch}
                )
            except httpx.ConnectError as exc:
                raise EmbeddingError(
                    f"cannot reach Ollama at {self._base_url} — is it running? "
                    f"(start it, then: ollama pull {self.model_name})"
                ) from exc
            if response.status_code >= 400:
                raise EmbeddingError(
                    f"Ollama embed failed ({response.status_code}): {response.text[:200]}"
                )
            vectors.extend(response.json()["embeddings"])
        return vectors
