# DriftGuard 🛡️

An AI agent that watches merged GitHub pull requests, detects when a code change
makes the documentation incorrect, proposes evidence-backed fixes, verifies them,
and opens a documentation PR for human review.

> Capstone project — Abdesslem Sfaihi, 2026.
> Proposal & architecture: see `DriftGuard-Proposal.html` (validated MVP perimeter:
> Python repo + Markdown docs, 4 drift types, measured on a planted-drift benchmark).

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (source .venv/bin/activate on Linux/Mac)
pip install -e ".[dev]"
copy .env.example .env            # then edit values

pytest                            # everything green?
uvicorn driftguard.api.webhook:app --reload --port 8000
```

Health check: <http://localhost:8000/health>

## Receiving real GitHub webhooks locally

GitHub cannot reach localhost. Use a delivery proxy during development:

```bash
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhooks/github
```

Then on the GitHub repo: Settings → Webhooks → Add webhook →
Payload URL = your smee channel, Content type = `application/json`,
Secret = the value of `GITHUB_WEBHOOK_SECRET`, event = *Pull requests*.

## Replaying archived events (no GitHub needed)

Every accepted delivery is archived in SQLite. Re-run any of them:

```bash
python -m driftguard.cli.replay list
python -m driftguard.cli.replay run <delivery_id>
```

This is the test harness, the debugger, and the offline demo fallback.

## Indexing the documentation (hybrid search)

Requires local Ollama with the embedding model: `ollama pull nomic-embed-text`.

```bash
python -m driftguard.cli.index_docs build --docs <repo-or-docs-dir>
python -m driftguard.cli.index_docs query "request_timeout default" -k 5
```

Query output shows, per hit, which retriever found it: `[D2]` = semantic
(FAISS) rank 2, `[B1]` = exact-word (BM25) rank 1. Results are fused with
Reciprocal Rank Fusion.

## Benchmark & evaluation

`benchmark/` contains a fake library (**pyconnect**) with realistic docs and
16 labeled cases: planted drifts of all four v1 kinds, a no-drift control,
and protected historical pages. The harness measures:

- **Fact recall** — did the AST extractor find every planted fact? Gate: 100%.
- **Retrieval recall@k** — did hybrid search surface every affected doc
  section? This is the system's ceiling: what retrieval misses, the judge
  can never fix. Target with real embeddings: ≥ 80%.

```bash
python -m driftguard.cli.evaluate --embedder fake     # offline; runs in CI
python -m driftguard.cli.evaluate --embedder ollama   # real numbers
```

Current scores: fact recall **100%**, retrieval recall **97.7%** (nomic-embed-text)
vs 88.4% (hash baseline) — the +9.3pt delta is the measured value of semantic
embeddings. The judgment stage (week 2) extends the same harness with
precision and protected-page verdicts.

## Project layout

```
src/driftguard/
├── api/        webhook gate: signature check, dedupe, archive     (week 1 ✓)
├── storage/    SQLite: deliveries + drift facts                   (week 1 ✓)
├── github/     GitHub REST client (files, contents at a ref)      (week 1 ✓)
├── analysis/   AST drift-fact extractor + PR pipeline             (week 1 ✓)
├── indexing/   markdown chunking, FAISS + BM25 hybrid index       (week 1 ✓)
├── agent/      handler seam → LangGraph agent                     (week 2)
└── cli/        replay + index tools                               (week 1 ✓)
```
