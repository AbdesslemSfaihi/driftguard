# The presentation demo: TinyBank

A runbook for the defense. It covers the story to tell, the one-time setup,
the day-of checklist, the four-minute script, a second act for follow-up
questions, what to do when something fails, and the questions a jury tends
to ask.

## The story in three sentences

A bank writes its rules twice: once for people (a handbook) and once for the
computer (code). When a rule changes, the developer changes the code and
merges it; the handbook quietly keeps saying the old thing. DriftGuard watches
every merge, finds the handbook sentences the change made false, and opens a
pull request that fixes them with the evidence attached; when a sentence does
not say which rule it describes, DriftGuard asks the maintainer instead of
guessing.

## Why TinyBank

| Need | How TinyBank meets it |
|---|---|
| A rule anyone understands | "You need at least 100 EUR to open an account." |
| A change anyone believes | A pricing review raises it to 150 EUR. Rules about money change all the time. |
| The doc ↔ code link visible at a glance | The handbook says **100 EUR** (`MINIMUM_DEPOSIT`); the code says `MINIMUM_DEPOSIT = 100`. |
| All three behaviours in one run | Handbook: fixed. FAQ: asked about. Changelog: left alone. |
| Repeatable on stage | `reset` puts the repository back; every rehearsal runs the same way. |

The repository is tiny on purpose. One code file, four documentation pages.
Nobody has to understand a connection pool to follow it.

### The rule in its two places

| In the handbook (`docs/account-rules.md`) | In the code (`src/tinybank/accounts.py`) |
|---|---|
| "A customer opens an account by paying a first deposit of at least **100 EUR** (`MINIMUM_DEPOSIT`)." | `MINIMUM_DEPOSIT = 100` |
| "at most **1000 EUR per day** (`DAILY_WITHDRAWAL_LIMIT`)" | `DAILY_WITHDRAWAL_LIMIT = 1000` |
| "a fee of **2 EUR** (`TRANSFER_FEE`)" | `TRANSFER_FEE = 2` |

Three more pages complete the picture:

- `docs/faq.md` is written for customers. "How much money do I need to open an
  account? A first deposit of at least 100 EUR." It states the value but never
  names the rule. DriftGuard cannot prove which rule it describes, so it asks.
- `docs/changelog.md` says accounts opened with 100 EUR in version 1.0.0. That
  was true. DriftGuard must recognise history and never edit it.
- `docs/getting-started.md` shows developers how to call each function. Its
  examples use amounts well above the minimum, so the headline change does not
  make them wrong. The judge looks at them and leaves them alone.

## One-time setup

1. On GitHub, create a **private** repository `tinybank-demo` under your
   account, with "Add a README file" ticked (so the default branch exists).
2. Edit the fine-grained personal access token DriftGuard already uses
   (GitHub → Settings → Developer settings → Fine-grained tokens): under
   *Repository access*, add `tinybank-demo`. Permissions stay as they are:
   Contents read/write, Pull requests read/write.
3. On the new repository: Settings → Webhooks → Add webhook. Payload URL =
   the smee channel already used by `pyconnect-demo`, content type
   `application/json`, secret = `GITHUB_WEBHOOK_SECRET` from `.env`, events =
   **Pull requests** and **Issue comments**. One smee channel can serve both
   repositories; DriftGuard keys everything by repository name.
4. Push the baseline. From the DriftGuard folder, with the venv active:

   ```bash
   python -m driftguard.cli.demo reset
   ```

   This creates every file of `demo/tinybank/` on `main`. Open the repository
   on GitHub and read the handbook once: it should read like a bank's page,
   not like code.

5. Rehearse the full script below at least twice, and write down how long
   the run takes on your machine. That number goes on the slide.

If you ever want a different repository name, pass `--repo owner/name` to
every command.

## Day-of checklist

Five things must be running, in this order:

1. Ollama (`ollama serve`, or the desktop app) with `nomic-embed-text` pulled.
2. n8n with the DNS fix loaded, the workflow published.
3. The DriftGuard server: `uvicorn driftguard.api.webhook:app --port 8000`.
4. The smee relay: `npx smee-client --url https://smee.io/<channel> --target http://localhost:8000/webhooks/github`.
5. A valid GitHub token and a valid Anthropic key in `.env` (top up the credit
   the day before).

Then:

```bash
python -m driftguard.cli.demo preflight
```

Every line must read `OK` or `LOOK`. A `FAIL` line names what to fix. The
command also confirms the repository is at the baseline; if it is not, run
`reset`. Keep two browser tabs open before you start: the story page list at
`http://localhost:8000/ui` and the handbook page on GitHub.

## The script (about four minutes)

**0:00. The problem.** Show the handbook section "Opening an account" on
GitHub, then the code line `MINIMUM_DEPOSIT = 100` next to it. Say: "Two
places, one truth. Now the business changes the rule."

**0:30. The change.** In the terminal:

```bash
python -m driftguard.cli.demo merge minimum-deposit
```

Show the merged pull request on GitHub: one line changed, 100 became 150.
Say: "This is what a developer does every day. Nobody opened the handbook."

**1:00. DriftGuard at work.** Open the story page for the new delivery (the
list at `/ui` shows it at the top). The stepper advances through *searching*,
*judging*, *asking*, *fixing*, *verifying*, *publishing* while you talk:

- *searching*: "It found the six sections that might talk about this rule."
- *judging*: "For each one it decided: wrong now, still right, history, or
  cannot tell." Point at the four verdicts: handbook **drifted**, FAQ
  **unsure**, changelog **historical**, getting-started **accurate**.
- *fixing* and *verifying*: "The model rewrote the handbook sentence. Then
  plain code checked the rewrite: 100 gone, 150 present, heading intact. The
  model never grades its own work."

**2:00. The result.** Open the docs pull request. The diff is two characters
in one sentence. Say: "Evidence attached: which merge, which fact, which
sentence. A human still clicks merge." If the n8n e-mail has arrived, show it:
"The team hears about it without watching a dashboard."

**2:30. The question.** Open the merged pull request. DriftGuard commented:
`Q1: Is this FAQ answer about MINIMUM_DEPOSIT?` Say: "The FAQ says 100 EUR but
never says which rule. Guessing would be how you lose a team's trust. So it
asks." Reply as the maintainer:

```bash
python -m driftguard.cli.demo answer <pr-number>
```

**3:00. The follow-up.** The story page gains a second act: the reply was read
as a confirmation, the FAQ section was located again in today's docs, fixed,
verified, and a second docs pull request cites the maintainer's answer as the
evidence. Say: "The model translated the reply. The human decided."

**3:30. What it did not touch.** Back on the story page, point at the
changelog verdict (*historical*) and at the getting-started sections
(*accurate*). Say: "Precision is the product. A tool that rewrites your
release notes gets uninstalled in a week."

Close with the measured numbers from the benchmark (precision, recall, ask
recall), which are in the README.

## A second act, if asked "does it only handle numbers?"

Each scenario is one realistic pull request and one drift kind. Run `reset`
first if the headline scenario has already been merged on the same baseline
file; otherwise they chain.

| Scenario | The pull request | What the docs get wrong |
|---|---|---|
| `rename-first-deposit` | `first_deposit` becomes `opening_deposit` | the getting-started example passes `first_deposit=500` |
| `instant-by-default` | transfers become instant by default | "Transfers are standard by default" and `instant=False` in the guide |
| `withdraw-needs-reason` | every withdrawal needs a reason | the guide calls `withdraw(alice, 200)` without one |
| `remove-close-account` | `close_account()` is removed | the guide still shows how to call it |

The rename is the most visual second act: the diff of the docs pull request
changes an identifier in a code example and leaves the English words "first
deposit" alone, which is the context-aware verifier doing its job. The
`instant-by-default` scenario usually costs the fixer one rejected attempt
(the word `False` must disappear from the section entirely); the story page
shows the attempt count and the verifier's feedback, which is a feature, not
a bug. Rehearse any scenario you intend to show.

## When something fails

- **No delivery arrives** (`wait` times out): smee is not forwarding, or the
  webhook is not subscribed to the right event. Check the smee terminal, then
  GitHub → Settings → Webhooks → Recent deliveries, and redeliver from there.
- **The run ends in `agent_error`**: usually Ollama not running or an invalid
  Anthropic key. Fix the cause, then replay the archived delivery without
  touching GitHub: `python -m driftguard.cli.replay run <delivery_id>`.
- **The judge did not ask about the FAQ** (it said `drifted` and fixed it):
  that happens; the judge runs at the SDK's default temperature. The docs
  pull request then fixes two files, which is still a correct outcome. Say so,
  and show the ask act on an archived story page instead.
- **Everything is down**: open an archived story page from a rehearsal. Every
  story page is built from stored rows, so it replays in full offline.
  Bookmark the best rehearsal.

## Questions a jury tends to ask

**"Can it go the other way: the handbook changes, does the code still follow
it?"** No, and on purpose. A code diff can be turned into objective facts by
parsing it (this constant changed from 100 to 150), and facts can be verified
by code. A sentence in a handbook cannot be parsed into a fact without a model
guessing what it means, and a guess is exactly what this system refuses to
act on. The direction code → docs is where the deterministic foundation
exists; the other direction is where the 90 % precision contract would be
lost first.

**"Why not let the model decide everything?"** The model has two narrow jobs:
judge what a known fact means for a given section, and rewrite one section.
What changed comes from the parser; whether the rewrite is right comes from
plain checks. Each stage that is code is a stage that cannot hallucinate.

**"What does it not catch?"** Behaviour that is not visible in a signature or
a constant, new functions nobody documented yet, nested classes and
keyword-only parameters. The perimeter grew only when a benchmark case showed
a gap, and these did not come up yet.

**"What does a run cost?"** A handful of model calls per merged pull request:
one judgment per fact, one rewrite per drifted section, one read per
maintainer reply. The benchmark reports in `benchmark/reports/` give the per-run
figures; the embedding model runs locally for free.

**"Why ask instead of fix?"** Because the first measurement said the model
would have rewritten the FAQ on a guess, every time. Asking turned a 0 % ask
recall into 80 % with the same model, and a wrong edit into a question a
maintainer answers from memory.
