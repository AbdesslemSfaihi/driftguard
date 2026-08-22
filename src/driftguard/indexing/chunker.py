"""Markdown chunking — header-aware, fence-safe, size-bounded.

Why not fixed-size windows (the classic RecursiveCharacterTextSplitter move):
documentation already has meaningful boundaries — its headings. A chunk that
starts mid-sentence in one section and ends mid-table in the next embeds as
mush and retrieves as mush. So the unit of retrieval here is "a section, or a
paragraph-packed window of a long section", each carrying its heading
breadcrumb ("Configuration > Timeouts") as context.

No overlap between windows (v1): heading boundaries already align with
meaning. If the benchmark shows recall misses at window seams, overlap is a
one-line addition — a decision the metrics will make, not a guess.
"""

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class DocChunk:
    doc_path: str  # "docs/configuration.md" (posix, relative to the docs root)
    heading_path: str  # "Configuration > Timeouts", or "(intro)" before any heading
    text: str  # the chunk content (includes its heading line when it has one)
    chunk_id: str  # "docs/configuration.md::3" — stable within one build


def chunk_markdown(text: str, doc_path: str, *, max_chars: int = 1800) -> list[DocChunk]:
    sections = _split_into_sections(text)
    chunks: list[DocChunk] = []
    for heading_path, section_text in sections:
        for window in _pack_paragraphs(_paragraphs(section_text), max_chars):
            if not window.strip():
                continue
            chunks.append(
                DocChunk(
                    doc_path=doc_path,
                    heading_path=heading_path,
                    text=window,
                    chunk_id=f"{doc_path}::{len(chunks)}",
                )
            )
    return chunks


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split at every heading, tracking the heading stack for breadcrumbs.

    Heading-looking lines inside ``` fences are content, not structure —
    markdown about markdown is common in developer docs.
    """
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    current_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        if current_lines:
            breadcrumb = " > ".join(title for _, title in stack) or "(intro)"
            sections.append((breadcrumb, "\n".join(current_lines)))

    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            flush()
            current_lines = [line]
            level, title = len(match.group(1)), match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            current_lines.append(line)
    flush()
    return sections


def _paragraphs(section_text: str) -> list[str]:
    """Blank-line-separated paragraphs, with fenced code blocks kept atomic."""
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in section_text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _pack_paragraphs(paragraphs: list[str], max_chars: int) -> list[str]:
    """Greedily pack whole paragraphs into windows of at most max_chars.

    A single oversized paragraph (a huge table, a long code block) becomes its
    own window rather than being cut mid-structure — better one big coherent
    chunk than two broken ones.
    """
    windows: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph) > max_chars:
            windows.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph) + 2
    if current:
        windows.append("\n\n".join(current))
    return windows
