# DriftGuard 🛡️

An AI agent that watches merged GitHub pull requests, detects when a code change
makes the documentation incorrect, proposes evidence-backed fixes, verifies them,
and opens a documentation PR for human review.

> Capstone project — Abdesslem Sfaihi, 2026.
> Perimeter validated with the supervisor (proposal document kept outside this
> repo): Python repos + Markdown docs, five drift kinds on module-level functions,
> top-level classes and UPPER_CASE constants, measured on a planted-drift benchmark.

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
Secret = the value of `GITHUB_WEBHOOK_SECRET`, events = *Pull requests* **and**
*Issue comments* (the second one carries developers' answers to DriftGuard's
questions; without it the ask flow can never resume).

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
26 labeled cases: planted drifts of all five kinds (removed, renamed, default
changed, signature changed, constant changed), no-drift controls, hard judge
cases (name collisions, inline history, English homonyms), planted-ambiguous
sections, and protected historical pages. The harness measures:

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

Measured scores (26 cases, nomic-embed-text, k=5; full reports in
`benchmark/reports/`):

| Stage | Metric | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|
| extraction | fact recall (gate 100%) | 100% | 100% |
| retrieval | recall@5 (hash baseline 90.9%) | 98.2% | 98.2% |
| judgment | **precision** (trust metric, target ≥ 90%) | **100%** | 96.4% |
| judgment | recall | 98.2% | 98.2% |
| asking | ask recall | 80% | 80% |
| asking | ask precision | 80%* | 100% |
| protected pages | never edited | clean | clean |

\* Sonnet's one needless question was about a rename; the code rule added
right after (only a changed *value* can be ambiguous) removes that class of
question. The one ask miss on both models is a retrieval miss, reported as
such by the harness: the FAQ says "retried", the queries say "retries".

The first ask measurement was **0%** — both judges called every
planted-ambiguous section drifted, and a live run would have rewritten the FAQ
on a guess. Two prompt iterations plus one code-enforced rule brought it to
80%; the history of `benchmark/reports/ask-*.json` tells that story. The judge
runs at the SDK's default temperature (the structured-output helper exposes no
other), so identical inputs can get different verdicts from one run to the next.

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

## The story page

`http://localhost:8000/ui` lists every delivery; each opens a page that
replays what the agent did with that merged PR, in the order it happened: the
change and where it was made, the doc sections retrieval found and which
retriever found them, the judge's verdict on every section including the ones
left alone, each fix with its diff, attempt count and verifier feedback, the
docs PR, and the question-and-answer thread with its follow-up PR. The page
polls while a run is in progress, so a live demo shows the pipeline advancing
stage by stage. Nothing is recomputed: every line comes from rows the agent
stored while working (schema v4 adds the verdict trail).

## Live validation (demo repository)

On the private demo repo `pyconnect-demo`, merged PR #7 (`deliver()` attempts
3 → 5) produced in one run: docs PR #8 fixing the page that names `deliver()`
(two words changed, the "Migrating from 0.x" narrative left alone) and question
Q1 on PR #7 about the FAQ answer that names no function. The maintainer's reply
`Q1: yes, that FAQ answer is about deliver()` arrived as an `issue_comment`
webhook and became follow-up PR #9, citing the answer as evidence. Only
comments from OWNER / MEMBER / COLLABORATOR accounts count as answers.
`scripts/demo/live_round_trip.py` drives this sequence; every delivery can be
replayed offline with `python -m driftguard.cli.replay`.

## Project layout

```
src/driftguard/
├── api/        webhook gate (week 1 ✓) + read-only story page   (week 4 ✓)
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
