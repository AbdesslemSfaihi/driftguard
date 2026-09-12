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

- **Judgment** (needs `ANTHROPIC_API_KEY`) — of the retrieved sections, which
  did the judge mark drifted? Reported as recall, **precision** (the trust
  metric: target ≥ 90%), and **protected pages** — release notes the judge
  must classify `historical`, never edit.
- **Asking** — planted-ambiguous sections (undecidable on purpose: a value two
  functions could own) where the correct verdict is `unsure` + a question.
  **Ask recall** = escalated instead of guessed; **ask precision** = questions
  that were warranted. Without this, a judge could game precision by asking
  about everything.

```bash
python -m driftguard.cli.evaluate --embedder fake              # offline; runs in CI
python -m driftguard.cli.evaluate --embedder ollama            # real retrieval numbers
python -m driftguard.cli.evaluate --embedder ollama --judge anthropic  # + judgment
```

Current scores: fact recall **100%**, retrieval recall **97.7%** (nomic-embed-text)
vs 88.4% (hash baseline) — the +9.3pt delta is the measured value of semantic
embeddings.

## The agent (week 2)

After facts are extracted, a LangGraph state machine runs
`search → judge → fix → verify → publish`:

- **search** — fetches the repo's docs *at the merge commit*, builds the hybrid
  index in memory, retrieves top-k candidate sections per fact.
- **judge** (Claude, structured outputs) — per fact, classifies each candidate
  section `drifted` / `accurate` / `historical` / `unsure`. Historical pages
  (release notes, changelogs) are never touched. Facts hitting the same section
  are grouped into one rewrite.
- **ask** (week 3) — `unsure` verdicts become questions to the developer instead
  of guesses: one digest comment on the merged PR (`Q1: ...`), each question
  stored in SQLite. Clear findings continue unblocked — a slow answer never
  holds up a verified fix. When the developer replies (`Q1: yes, that's
  deliver()`), the answer arrives as an `issue_comment` webhook and a fresh
  run applies it: the reply is read into a decision (the model translates, the
  human decides — the judge is never re-run), the section is re-located in
  today's docs by heading, and a follow-up docs PR cites the answer as
  evidence. `ASK_DEVELOPER=false` stores questions without commenting;
  `MAX_QUESTIONS_PER_RUN` caps how often a human gets interrupted.
- **fix** (Claude) — rewrites the drifted section, minimal edits only.
- **verify** (deterministic code, no LLM) — the old name must be gone, the new
  default must appear, removed functions must not be mentioned... A failing
  fix goes back to **fix** with feedback (bounded by `MAX_FIX_ATTEMPTS`);
  a fix that never passes is dropped, not published.
- **publish** — branch `driftguard/docs-pr-<n>`, one commit per doc (edits are
  applied by chunk line-range, bottom-up), and an evidence-backed PR for human
  review. `OPEN_DOCS_PRS=false` stores verified proposals without pushing.

The LLM never decides *what changed* (the AST extractor did) and never grades
its own work (verification is code). Each credential gates one stage: no
`GITHUB_TOKEN` = archive only; no `ANTHROPIC_API_KEY` = facts only.

## Project layout

```
src/driftguard/
├── api/        webhook gate: signature check, dedupe, archive     (week 1 ✓)
├── storage/    SQLite (migrated): deliveries, facts, proposals    (week 1 ✓)
├── github/     GitHub REST client: read at a ref, branch/PR write (week 1 ✓)
├── analysis/   AST drift-fact extractor + PR pipeline             (week 1 ✓)
├── indexing/   markdown chunking, FAISS + BM25 hybrid index       (week 1 ✓)
├── agent/      LangGraph agent: search→judge→ask→fix→verify→publish
│               + answer processing (human in the loop)            (week 3 ✓)
├── evaluation/ planted-drift harness (facts, retrieval, judgment,
│               ask metrics)                                       (week 3 ✓)
└── cli/        replay + index + evaluate tools                    (week 1 ✓)
```
