"""Chunker + hybrid index tests — fully offline via a deterministic fake embedder."""

import hashlib

import numpy as np

from driftguard.indexing.chunker import chunk_markdown
from driftguard.indexing.index import HybridIndex

SAMPLE_MD = """Intro paragraph before any heading.

# Configuration

General configuration notes.

## Timeouts

Set `request_timeout` to control how long the client waits. Default: 30 seconds.

## Retries

The client retries failed calls twice.

```md
# not a real heading, just markdown inside a fence
```

More retry notes.
"""


class FakeEmbedder:
    """Deterministic bag-of-hashed-words vectors: texts sharing words are similar.

    Good enough to exercise the dense path without a model, and instant.
    """

    model_name = "fake"

    def __init__(self, dim: int = 64):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dim, dtype="float32")
            for token in text.lower().split():
                bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
            out.append((vec + 1e-6).tolist())  # epsilon: never a zero vector
        return out


# ---------- chunker ----------


def test_chunks_follow_headings_with_breadcrumbs():
    chunks = chunk_markdown(SAMPLE_MD, "docs/config.md")
    breadcrumbs = [c.heading_path for c in chunks]
    assert breadcrumbs == [
        "(intro)",
        "Configuration",
        "Configuration > Timeouts",
        "Configuration > Retries",
    ]
    assert all(c.doc_path == "docs/config.md" for c in chunks)
    assert chunks[2].text.startswith("## Timeouts")


def test_heading_inside_code_fence_is_not_a_boundary():
    chunks = chunk_markdown(SAMPLE_MD, "docs/config.md")
    retries = next(c for c in chunks if c.heading_path.endswith("Retries"))
    assert "not a real heading" in retries.text
    assert "More retry notes." in retries.text


def test_long_sections_split_on_paragraphs_not_mid_sentence():
    body = "\n\n".join(f"Paragraph number {i} with some words." for i in range(60))
    text = f"# Big\n\n{body}\n"
    chunks = chunk_markdown(text, "big.md", max_chars=300)
    assert len(chunks) > 1
    assert all(len(c.text) <= 300 or "\n\n" not in c.text for c in chunks)
    assert all(c.heading_path == "Big" for c in chunks)
    assert all(c.text.strip().endswith(".") for c in chunks)  # never cut mid-sentence


def test_chunk_ids_are_stable_and_unique():
    chunks = chunk_markdown(SAMPLE_MD, "docs/config.md")
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids == [f"docs/config.md::{i}" for i in range(len(chunks))]


# ---------- hybrid index ----------


def build_index() -> HybridIndex:
    index = HybridIndex(FakeEmbedder())
    index.build(chunk_markdown(SAMPLE_MD, "docs/config.md"))
    return index


def test_exact_identifier_query_is_won_by_bm25():
    index = build_index()
    results = index.search("request_timeout", k=3)
    top = results[0]
    assert "request_timeout" in top.chunk.text
    assert top.sparse_rank == 1  # the exact-match retriever found it first


def test_word_overlap_query_reaches_the_right_section_via_dense():
    index = build_index()
    results = index.search("how long the client waits seconds", k=3)
    assert any("Timeouts" in r.chunk.heading_path for r in results[:2])
    assert any(r.dense_rank is not None for r in results)


def test_snake_case_parts_match_partial_queries():
    index = build_index()
    results = index.search("timeout", k=3)
    assert any("request_timeout" in r.chunk.text for r in results)


def test_empty_index_returns_empty_results():
    index = HybridIndex(FakeEmbedder())
    index.build([])
    assert index.search("anything") == []


def test_save_and_load_roundtrip(tmp_path):
    index = build_index()
    before = index.search("request_timeout", k=3)
    index.save(tmp_path / "idx")

    reloaded = HybridIndex.load(tmp_path / "idx", FakeEmbedder())
    after = reloaded.search("request_timeout", k=3)

    assert [r.chunk.chunk_id for r in after] == [r.chunk.chunk_id for r in before]
    assert after[0].chunk.text == before[0].chunk.text
