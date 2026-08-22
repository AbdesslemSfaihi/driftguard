"""Scan a directory tree for markdown documentation and chunk it."""

from pathlib import Path

from driftguard.indexing.chunker import DocChunk, chunk_markdown

SKIP_DIRS = {"node_modules", ".git", ".venv", "__pycache__"}


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.md")
        if not any(part.startswith(".") or part in SKIP_DIRS for part in p.parts)
    )


def load_corpus(root: Path) -> list[DocChunk]:
    """Chunk every markdown file under *root*; doc_path is posix-relative to it."""
    chunks: list[DocChunk] = []
    for path in markdown_files(root):
        relative = path.relative_to(root).as_posix()
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), relative))
    return chunks
