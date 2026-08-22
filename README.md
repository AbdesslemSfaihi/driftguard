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

## Project layout

```
src/driftguard/
├── api/        webhook gate: signature check, dedupe, archive  (week 1 ✓)
├── storage/    SQLite persistence                              (week 1 ✓)
├── agent/      handler seam → LangGraph agent                  (week 2)
├── analysis/   drift-fact extractor — EXERCISE 01              (you!)
├── indexing/   docs loading, chunking, FAISS + BM25            (week 1-2)
└── cli/        replay tool                                     (week 1 ✓)
```

## Current exercise

**Exercise 01 — drift-fact extractor** (`src/driftguard/analysis/extractor.py`):
turn an old/new pair of Python sources into typed `DriftFact`s using the `ast`
module. The contract lives in `tests/test_extractor.py` — delete its `pytestmark`
skip line and make the tests pass one by one.
