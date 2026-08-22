"""Hybrid retrieval: FAISS (dense, meaning) + BM25 (sparse, exact words), RRF-fused.

Why hybrid is load-bearing here and not a buzzword: DriftGuard's queries are
drift facts. `request_timeout` is a LEXICAL token — semantic embeddings can
blur it into "timeout, retry, deadline" neighbours, while BM25 nails the exact
identifier. But docs also paraphrase ("how long the client waits before giving
up") — that's where embeddings win and BM25 sees zero term overlap. Each
retriever catches what the other misses; recall >= 80% is a contract metric.

Why Reciprocal Rank Fusion instead of mixing raw scores: cosine similarity and
BM25 scores live on incomparable scales — averaging them is meaningless. RRF
uses only each document's RANK in each list:  score(d) = sum over retrievers
of 1 / (K + rank_d).  K = 60 is the standard damping constant from the RRF
paper; it keeps a #1 rank from completely dominating.

Implemented by hand (~15 lines) rather than importing an ensemble class:
we keep per-retriever ranks for the evidence trace ("found by exact match,
rank 1"), and there is no framework version churn to defend at the jury.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from driftguard.indexing.chunker import DocChunk
from driftguard.indexing.embedder import Embedder

log = logging.getLogger("driftguard.indexing")

RRF_K = 60
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


class IndexModelMismatch(RuntimeError):
    """The saved index was embedded with a different model than the one querying."""


@dataclass(frozen=True)
class SearchResult:
    chunk: DocChunk
    score: float  # RRF-fused score (comparable only within one result list)
    dense_rank: int | None  # 1-based rank in the embedding search, if present
    sparse_rank: int | None  # 1-based rank in the BM25 search, if present


def _embed_text(chunk: DocChunk) -> str:
    # The breadcrumb gives short chunks their context back:
    # "default is 30" embeds very differently under "Configuration > Timeouts".
    return f"{chunk.heading_path}\n{chunk.text}"


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens; snake_case kept whole AND split into parts,
    so a query for `timeout` still reaches docs that say `request_timeout`."""
    tokens = _TOKEN_RE.findall(text.lower())
    expanded = list(tokens)
    for token in tokens:
        if "_" in token:
            expanded.extend(part for part in token.split("_") if part)
    return expanded


class HybridIndex:
    def __init__(self, embedder: Embedder):
        self._embedder = embedder
        self._chunks: list[DocChunk] = []
        self._dense: faiss.IndexFlatIP | None = None
        self._sparse: BM25Okapi | None = None

    def build(self, chunks: list[DocChunk]) -> None:
        self._chunks = list(chunks)
        if not self._chunks:
            self._dense = self._sparse = None
            return
        texts = [_embed_text(c) for c in self._chunks]
        vectors = np.array(self._embedder.embed(texts), dtype="float32")
        # Normalized vectors + inner product == cosine similarity: direction
        # (meaning) matters, magnitude (text length artifacts) does not.
        faiss.normalize_L2(vectors)
        self._dense = faiss.IndexFlatIP(vectors.shape[1])
        self._dense.add(vectors)
        self._sparse = BM25Okapi([_tokenize(t) for t in texts])
        log.info("indexed %d chunks (dim=%d)", len(self._chunks), vectors.shape[1])

    def search(self, query: str, k: int = 8) -> list[SearchResult]:
        if not self._chunks:
            return []
        k = min(k, len(self._chunks))

        query_vector = np.array(self._embedder.embed([query]), dtype="float32")
        faiss.normalize_L2(query_vector)
        _, dense_ids = self._dense.search(query_vector, k)
        dense_rank = {int(i): r + 1 for r, i in enumerate(dense_ids[0]) if i != -1}

        bm25_scores = self._sparse.get_scores(_tokenize(query))
        sparse_order = np.argsort(bm25_scores)[::-1][:k]
        # BM25 score 0 means "shares no term with the query" — that is not a
        # rank, it is an absence; including it would pollute the fusion.
        sparse_rank = {int(i): r + 1 for r, i in enumerate(sparse_order) if bm25_scores[i] > 0}

        fused: dict[int, float] = {}
        for idx, rank in dense_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)
        for idx, rank in sparse_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)

        top = sorted(fused.items(), key=lambda item: -item[1])[:k]
        return [
            SearchResult(
                chunk=self._chunks[idx],
                score=score,
                dense_rank=dense_rank.get(idx),
                sparse_rank=sparse_rank.get(idx),
            )
            for idx, score in top
        ]

    # ---------- persistence ----------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._dense, str(directory / "dense.faiss"))
        payload = {
            "embedding_model": self._embedder.model_name,
            "chunks": [asdict(c) for c in self._chunks],
        }
        (directory / "chunks.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path, embedder: Embedder) -> "HybridIndex":
        payload = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        if payload["embedding_model"] != embedder.model_name:
            # Vectors from one model live in that model's space; querying them
            # with another model's vectors returns garbage that LOOKS ranked.
            # Refuse outright: silently wrong results are worse than no results.
            raise IndexModelMismatch(
                f"index was built with {payload['embedding_model']!r} but queried "
                f"with {embedder.model_name!r} — rebuild the index"
            )
        index = cls(embedder)
        try:
            index._chunks = [DocChunk(**c) for c in payload["chunks"]]
        except TypeError as exc:
            # A chunks.json written before the DocChunk schema grew (e.g. the
            # line-range fields) — the index format changed, not the caller.
            raise IndexModelMismatch(
                f"saved index has an outdated chunk format — rebuild the index ({exc})"
            ) from exc
        index._dense = faiss.read_index(str(directory / "dense.faiss"))
        # BM25 is rebuilt from the chunk texts: it is cheap, and it avoids
        # pickling (a deserialization attack surface and a version trap).
        index._sparse = BM25Okapi([_tokenize(_embed_text(c)) for c in index._chunks])
        return index
