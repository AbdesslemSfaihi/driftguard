"""Build and query the documentation index from the command line.

    python -m driftguard.cli.index_docs build --docs <repo-or-docs-dir>
    python -m driftguard.cli.index_docs query "request_timeout default"

Build scans the directory recursively for *.md files (hidden folders and
node_modules skipped), chunks them, embeds with local Ollama, and saves the
index. Query loads the index and prints fused results with, for each hit,
WHICH retriever found it — [D2] = dense rank 2, [B1] = exact-match rank 1.
That visibility is the point: it is tomorrow's evidence trace.
"""

import argparse
import logging
import sys
from pathlib import Path

from driftguard.config import get_settings
from driftguard.indexing.chunker import chunk_markdown
from driftguard.indexing.embedder import OllamaEmbedder
from driftguard.indexing.index import HybridIndex

SKIP_DIRS = {"node_modules", ".git", ".venv", "__pycache__"}


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.md")
        if not any(part.startswith(".") or part in SKIP_DIRS for part in p.parts)
    )


def main() -> None:
    # Windows consoles default to a legacy codepage (cp1252); docs contain
    # emoji/accents, so force UTF-8 instead of crashing on the first snippet.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="driftguard-index", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="chunk + embed + index all *.md under --docs")
    build_cmd.add_argument("--docs", type=Path, required=True)
    build_cmd.add_argument("--out", type=Path, default=None)

    query_cmd = sub.add_parser("query", help="search the saved index")
    query_cmd.add_argument("text")
    query_cmd.add_argument("--index", type=Path, default=None)
    query_cmd.add_argument("-k", type=int, default=5)

    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    embedder = OllamaEmbedder(settings.ollama_base_url, settings.embedding_model)

    if args.command == "build":
        root = args.docs.resolve()
        chunks = []
        files = _markdown_files(root)
        for path in files:
            relative = path.relative_to(root).as_posix()
            chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), relative))
        index = HybridIndex(embedder)
        index.build(chunks)
        out = args.out or settings.index_dir
        index.save(out)
        print(f"indexed {len(chunks)} chunks from {len(files)} files -> {out}")

    elif args.command == "query":
        index = HybridIndex.load(args.index or settings.index_dir, embedder)
        for result in index.search(args.text, k=args.k):
            found_by = ""
            if result.dense_rank is not None:
                found_by += f"[D{result.dense_rank}]"
            if result.sparse_rank is not None:
                found_by += f"[B{result.sparse_rank}]"
            snippet = " ".join(result.chunk.text.split())[:110]
            print(
                f"{result.score:.4f} {found_by:10s} {result.chunk.doc_path} :: "
                f"{result.chunk.heading_path}\n           {snippet}"
            )


if __name__ == "__main__":
    main()
