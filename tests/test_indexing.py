"""Chunker + hybrid index tests — fully offline via the deterministic HashEmbedder."""

from driftguard.indexing.chunker import apply_chunk_edits, chunk_markdown
from driftguard.indexing.embedder import HashEmbedder
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


def test_chunks_carry_their_source_line_ranges():
    lines = SAMPLE_MD.splitlines()
    for chunk in chunk_markdown(SAMPLE_MD, "docs/config.md"):
        assert lines[chunk.start_line] == chunk.text.splitlines()[0]
        assert lines[chunk.end_line] == chunk.text.splitlines()[-1]


def test_apply_chunk_edits_replaces_exact_sections_and_nothing_else():
    chunks = chunk_markdown(SAMPLE_MD, "docs/config.md")
    timeouts = next(c for c in chunks if c.heading_path.endswith("Timeouts"))

    new_doc = apply_chunk_edits(SAMPLE_MD, [(timeouts, "## Timeouts\n\nBrand new text.")])

    assert "Brand new text." in new_doc
    assert "request_timeout" not in new_doc  # the old section content is gone
    assert "# Configuration" in new_doc  # everything around it survived
    assert "More retry notes." in new_doc
    assert "not a real heading" in new_doc  # fenced content intact


def test_apply_chunk_edits_is_bottom_up_safe_for_multiple_edits():
    chunks = chunk_markdown(SAMPLE_MD, "docs/config.md")
    timeouts = next(c for c in chunks if c.heading_path.endswith("Timeouts"))
    retries = next(c for c in chunks if c.heading_path.endswith("Retries"))

    # Passed top-down on purpose: apply_chunk_edits must reorder internally.
    new_doc = apply_chunk_edits(
        SAMPLE_MD, [(timeouts, "## Timeouts\n\nT."), (retries, "## Retries\n\nR.")]
    )
    assert "\nT.\n" in new_doc and "\nR." in new_doc
    assert "retries failed calls" not in new_doc


# ---------- hybrid index ----------


def build_index() -> HybridIndex:
    index = HybridIndex(HashEmbedder())
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
    index = HybridIndex(HashEmbedder())
    index.build([])
    assert index.search("anything") == []


def test_load_with_a_different_embedding_model_is_refused(tmp_path):
    import pytest

    from driftguard.indexing.index import IndexModelMismatch

    index = build_index()
    index.save(tmp_path / "idx")

    other = HashEmbedder()
    other.model_name = "some-other-model"
    with pytest.raises(IndexModelMismatch, match="rebuild the index"):
        HybridIndex.load(tmp_path / "idx", other)


def test_save_and_load_roundtrip(tmp_path):
    index = build_index()
    before = index.search("request_timeout", k=3)
    index.save(tmp_path / "idx")

    reloaded = HybridIndex.load(tmp_path / "idx", HashEmbedder())
    after = reloaded.search("request_timeout", k=3)

    assert [r.chunk.chunk_id for r in after] == [r.chunk.chunk_id for r in before]
    assert after[0].chunk.text == before[0].chunk.text
