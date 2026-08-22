"""Markdown chunking — header-aware, fence-safe, size-bounded.

Why not fixed-size windows (the classic RecursiveCharacterTextSplitter move):
documentation already has meaningful boundaries — its headings. A chunk that
starts mid-sentence in one section and ends mid-table in the next embeds as
mush and retrieves as mush. So the unit of retrieval here is "a section, or a
paragraph-packed window of a long section", each carrying its heading
breadcrumb ("Configuration > Timeouts") as context.

Each chunk also records the LINE RANGE it came from. The agent's fixer
rewrites a chunk; mapping that rewrite back onto the original file must be
exact, and chunk text is normalized (multiple blank lines collapse to one),
so text-replace would silently fail. Line ranges cannot.

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
    start_line: int  # 0-based, inclusive — first line of the chunk in the source file
    end_line: int  # 0-based, inclusive — last line of the chunk in the source file


@dataclass(frozen=True)
class _Paragraph:
    text: str
    start_line: int
    end_line: int


def chunk_markdown(text: str, doc_path: str, *, max_chars: int = 1800) -> list[DocChunk]:
    sections = _split_into_sections(text)
    chunks: list[DocChunk] = []
    for heading_path, section_lines in sections:
        paragraphs = _paragraphs(section_lines)
        for window in _pack_paragraphs(paragraphs, max_chars):
            window_text = "\n\n".join(p.text for p in window)
            if not window_text.strip():
                continue
            chunks.append(
                DocChunk(
                    doc_path=doc_path,
                    heading_path=heading_path,
                    text=window_text,
                    chunk_id=f"{doc_path}::{len(chunks)}",
                    start_line=window[0].start_line,
                    end_line=window[-1].end_line,
                )
            )
    return chunks


def apply_chunk_edits(text: str, edits: list[tuple[DocChunk, str]]) -> str:
    """Replace each chunk's line range in *text* with its new markdown.

    Applied bottom-up (highest start_line first) so earlier replacements never
    shift the line numbers of the ones still to come. Chunks never overlap by
    construction, which is what makes this safe.
    """
    lines = text.splitlines()
    for chunk, new_text in sorted(edits, key=lambda edit: -edit[0].start_line):
        lines[chunk.start_line : chunk.end_line + 1] = new_text.splitlines()
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _split_into_sections(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Split at every heading, tracking the heading stack for breadcrumbs.

    Lines are carried as (line_number, line) pairs so chunks can report where
    they came from. Heading-looking lines inside ``` fences are content, not
    structure — markdown about markdown is common in developer docs.
    """
    sections: list[tuple[str, list[tuple[int, str]]]] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    current_lines: list[tuple[int, str]] = []
    in_fence = False

    def flush() -> None:
        if current_lines:
            breadcrumb = " > ".join(title for _, title in stack) or "(intro)"
            sections.append((breadcrumb, list(current_lines)))

    for number, line in enumerate(text.splitlines()):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            flush()
            current_lines = [(number, line)]
            level, title = len(match.group(1)), match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            current_lines.append((number, line))
    flush()
    return sections


def _paragraphs(section_lines: list[tuple[int, str]]) -> list[_Paragraph]:
    """Blank-line-separated paragraphs, with fenced code blocks kept atomic."""
    paragraphs: list[_Paragraph] = []
    current: list[tuple[int, str]] = []
    in_fence = False

    def flush() -> None:
        if current:
            paragraphs.append(
                _Paragraph(
                    text="\n".join(line for _, line in current),
                    start_line=current[0][0],
                    end_line=current[-1][0],
                )
            )

    for number, line in section_lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            flush()
            current = []
        else:
            current.append((number, line))
    flush()
    return paragraphs


def _pack_paragraphs(paragraphs: list[_Paragraph], max_chars: int) -> list[list[_Paragraph]]:
    """Greedily pack whole paragraphs into windows of at most max_chars.

    A single oversized paragraph (a huge table, a long code block) becomes its
    own window rather than being cut mid-structure — better one big coherent
    chunk than two broken ones.
    """
    windows: list[list[_Paragraph]] = []
    current: list[_Paragraph] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph.text) > max_chars:
            windows.append(current)
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph.text) + 2
    if current:
        windows.append(current)
    return windows
