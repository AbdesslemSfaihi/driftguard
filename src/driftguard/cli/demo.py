"""Drive the presentation demo (TinyBank) against the live demo repository.

    python -m driftguard.cli.demo preflight               # everything the live demo needs, checked
    python -m driftguard.cli.demo reset                   # demo repo back to the baseline
    python -m driftguard.cli.demo merge minimum-deposit   # open AND merge the scripted pull request
    python -m driftguard.cli.demo wait 3                  # follow what the server did with PR #3
    python -m driftguard.cli.demo answer 3                # reply to Q1 as the maintainer
    python -m driftguard.cli.demo wait 3 --event issue_comment

The baseline (demo/tinybank in this repository) is the truth the demo starts
from. A scenario is one realistic code change applied to it: the kind of pull
request a developer merges when a business rule changes. `reset` makes every
rehearsal repeatable: it rewrites the baseline files on the default branch,
closes the pull requests DriftGuard opened and deletes the branches both sides
created. The scenario table is tested offline (tests/test_demo_cli.py): each
edit must apply to the baseline exactly once and must yield the drift facts
the demo narrative promises.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic
import httpx

from driftguard.config import Settings, get_settings
from driftguard.github.client import GitHubClient, GitHubError
from driftguard.storage import db
from driftguard.story import FINAL_STATUSES

DEMO_REPO = "AbdesslemSfaihi/tinybank-demo"
BASELINE = Path("demo/tinybank")
CODE_FILE = "src/tinybank/accounts.py"
BRANCH_PREFIXES = ("demo/", "driftguard/")  # ours and DriftGuard's; both are swept by `reset`


class DemoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Edit:
    old: str
    new: str
    count: int = 1  # occurrences the baseline must contain; anything else = not at the baseline


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str  # the pull request title, as a developer would write it
    body: str
    edits: tuple[Edit, ...]
    expected_facts: tuple[tuple[str, str], ...]  # (kind, symbol) the extractor must produce
    answer: str | None = None  # the maintainer's reply when the run asks about the FAQ

    def apply(self, source: str) -> str:
        for edit in self.edits:
            found = source.count(edit.old)
            if found != edit.count:
                raise DemoError(
                    f"scenario {self.key!r}: expected {edit.count} occurrence(s) of"
                    f" {edit.old[:40]!r} in {CODE_FILE}, found {found}."
                    " The repo is not at the baseline: run `demo reset` first."
                )
            source = source.replace(edit.old, edit.new)
        return source


CLOSE_ACCOUNT_SOURCE = '''

def close_account(account):
    """Close an account. Only an account with a zero balance can be closed."""
    if account.balance != 0:
        raise ValueError("the balance must be zero before closing")
    account.closed = True
    return account
'''

WITHDRAW_OLD = (
    "def withdraw(account, amount):\n"
    '    """Take money out of an account, within DAILY_WITHDRAWAL_LIMIT and the balance."""'
)
WITHDRAW_NEW = (
    "def withdraw(account, amount, reason):\n"
    '    """Take money out of an account, within DAILY_WITHDRAWAL_LIMIT and the balance.\n'
    "\n"
    "    Every withdrawal must state a reason; it is kept for the compliance report.\n"
    '    """\n'
    "    if not reason:\n"
    '        raise ValueError("every withdrawal must state a reason")'
)

SCENARIOS: dict[str, Scenario] = {
    scenario.key: scenario
    for scenario in (
        Scenario(
            key="minimum-deposit",
            title="Raise the minimum first deposit to 150 EUR",
            body=(
                "Decision from the pricing review: a new account now needs a first deposit"
                " of at least 150 EUR (was 100)."
            ),
            edits=(Edit("MINIMUM_DEPOSIT = 100", "MINIMUM_DEPOSIT = 150"),),
            expected_facts=(("constant_changed", "MINIMUM_DEPOSIT"),),
            answer=(
                "Q1: yes - that FAQ answer describes MINIMUM_DEPOSIT, so 100 EUR is now outdated."
            ),
        ),
        Scenario(
            key="rename-first-deposit",
            title="Rename first_deposit to opening_deposit",
            body="Aligns the code with the term the product team uses everywhere else.",
            edits=(Edit("first_deposit", "opening_deposit", count=3),),
            expected_facts=(("renamed", "open_account.first_deposit"),),
        ),
        Scenario(
            key="instant-by-default",
            title="Make transfers instant by default",
            body="Instant transfers are now the default; standard ones stay available.",
            edits=(Edit("instant=False", "instant=True"),),
            expected_facts=(("default_changed", "transfer.instant"),),
        ),
        Scenario(
            key="withdraw-needs-reason",
            title="Require a reason for every withdrawal",
            body="Compliance: every withdrawal must carry a reason for the quarterly report.",
            edits=(Edit(WITHDRAW_OLD, WITHDRAW_NEW),),
            expected_facts=(("signature_changed", "withdraw"),),
        ),
        Scenario(
            key="remove-close-account",
            title="Remove close_account(): closing moves to the branch process",
            body="Accounts are now closed by an advisor; the library no longer offers it.",
            edits=(Edit(CLOSE_ACCOUNT_SOURCE, "\n"),),
            expected_facts=(("removed", "close_account"),),
        ),
    )
}


def baseline_files(baseline: Path) -> dict[str, str]:
    """Every file of the demo repository, keyed by its path inside that repo."""
    files: dict[str, str] = {}
    for path in sorted(baseline.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(baseline).as_posix()] = path.read_text(encoding="utf-8")
    if not files:
        raise DemoError(f"no baseline files under {baseline}")
    return files


# ---------- commands ----------


def merge(client: GitHubClient, repo: str, scenario: Scenario) -> tuple[int, str]:
    """Open the scenario's pull request and merge it at once, as a developer
    would with the GitHub button. Returns (number, html_url)."""
    base = client.default_branch(repo)
    base_sha = client.ref_sha(repo, base)
    current = client.file_with_sha(repo, CODE_FILE, base)
    if current is None:
        raise DemoError(f"{CODE_FILE} is missing on {base}: run `demo reset` first")
    text, blob_sha = current
    branch = f"demo/{scenario.key}"
    client.create_branch(repo, branch, base_sha)
    client.put_file(
        repo,
        CODE_FILE,
        message=scenario.title,
        text=scenario.apply(text),
        branch=branch,
        sha=blob_sha,
    )
    url = client.open_pull_request(
        repo, title=scenario.title, body=scenario.body, head=branch, base=base
    )
    number = int(url.rstrip("/").rsplit("/", 1)[1])
    client.merge_pull_request(repo, number)
    return number, url


def reset(client: GitHubClient, repo: str, baseline: Path) -> list[str]:
    """Put the demo repository back to the baseline. Returns what was done."""
    done: list[str] = []
    base = client.default_branch(repo)
    for path, text in baseline_files(baseline).items():
        current = client.file_with_sha(repo, path, base)
        if current is not None and current[0] == text:
            continue
        client.put_file(
            repo,
            path,
            message=f"chore(demo): reset {path} to the baseline",
            text=text,
            branch=base,
            sha=None if current is None else current[1],
        )
        done.append(f"{'created' if current is None else 'restored'} {path}")
    for pr in client.open_pull_requests(repo):
        if pr.head.startswith(BRANCH_PREFIXES):
            client.close_pull_request(repo, pr.number)
            done.append(f"closed PR #{pr.number} ({pr.head})")
    for prefix in BRANCH_PREFIXES:
        for branch in client.branches_with_prefix(repo, prefix):
            client.delete_branch(repo, branch)
            done.append(f"deleted branch {branch}")
    return done


def wait(settings: Settings, repo: str, pr_number: int, event: str, timeout: int) -> None:
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        rows = [
            r
            for r in db.deliveries_for_pr(settings.database_path, repo, pr_number)
            if r["event"] == event
        ]
        row = rows[-1] if rows else None
        if row is not None and row["status"] in FINAL_STATUSES:
            break
        time.sleep(5)
    if row is None:
        raise DemoError(
            "no delivery arrived: is the server up, is smee forwarding, and is the webhook"
            f" subscribed to {event!r} events?"
        )
    story = f"{settings.public_base_url.rstrip('/')}/ui/deliveries/{row['delivery_id']}"
    print(f"delivery {row['delivery_id']}  status={row['status']}")
    print(f"  docs PR: {row['docs_pr_url'] or '-'}")
    print(f"  story:   {story}")
    for q in db.questions_for_delivery(settings.database_path, row["delivery_id"]):
        print(f"  {q['q_label']} [{q['status']}] {q['doc_path']} :: {q['heading']}")
        print(f"     {q['question']}" + (f"  -> answer: {q['answer']}" if q["answer"] else ""))
    for p in db.proposals_for_delivery(settings.database_path, row["delivery_id"]):
        print(f"  proposal [{p['status']}] {p['doc_path']} :: {p['heading']}  ({p['facts']})")


def preflight(settings: Settings, repo: str, baseline: Path) -> bool:
    """Check what can be checked, list what must be eyeballed. True = all green."""
    results: list[tuple[str, str, str]] = []

    def check(name: str, probe: Callable[[], str]) -> None:
        try:
            results.append(("OK", name, probe()))
        except Exception as exc:  # any failure is a demo-day blocker worth one line
            results.append(("FAIL", name, str(exc)[:160]))

    def github_probe() -> str:
        if not settings.github_token:
            raise DemoError("GITHUB_TOKEN is empty in .env")
        with GitHubClient(settings.github_token) as client:
            branch = client.default_branch(repo)
            current = client.file_with_sha(repo, CODE_FILE, branch)
        if current is None:
            raise DemoError(f"{CODE_FILE} not found on {repo}: run `demo reset`")
        if current[0] != baseline_files(baseline)[CODE_FILE]:
            raise DemoError(f"{CODE_FILE} differs from the baseline: run `demo reset`")
        return f"{repo} is at the baseline (default branch {branch})"

    def anthropic_probe() -> str:
        if not settings.anthropic_api_key:
            raise DemoError("ANTHROPIC_API_KEY is empty in .env")
        model = anthropic.Anthropic(api_key=settings.anthropic_api_key).models.retrieve(
            settings.judge_model
        )
        return f"key accepted, judge model {model.id}"

    def ollama_probe() -> str:
        response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        response.raise_for_status()
        names = [m.get("name", "") for m in response.json().get("models", [])]
        if not any(name.startswith(settings.embedding_model) for name in names):
            raise DemoError(f"model {settings.embedding_model!r} not pulled (have {names})")
        return f"up, {settings.embedding_model} available"

    def server_probe() -> str:
        response = httpx.get(f"{settings.public_base_url.rstrip('/')}/health", timeout=5)
        response.raise_for_status()
        return f"up at {settings.public_base_url}"

    def n8n_probe() -> str:
        if not settings.notify_webhook_url:
            return "notifications are OFF (NOTIFY_WEBHOOK_URL empty): no e-mail will go out"
        origin = httpx.URL(settings.notify_webhook_url).copy_with(path="/", query=None)
        httpx.get(origin, timeout=5)  # any HTTP answer at all means n8n is listening
        return f"listening at {origin}"

    check("GitHub token + demo repo", github_probe)
    check("Anthropic key + judge model", anthropic_probe)
    check("Ollama + embedding model", ollama_probe)
    check("DriftGuard server", server_probe)
    check("n8n", n8n_probe)
    results.append(
        ("LOOK", "smee relay", "its terminal must say 'Connected' to the channel in the webhook")
    )
    results.append(("LOOK", "n8n workflow", "published, with the DNS fix loaded (NODE_OPTIONS)"))
    for status, name, detail in results:
        print(f"[{status:4}] {name}: {detail}")
    return all(status != "FAIL" for status, _, _ in results)


# ---------- entry point ----------


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="driftguard-demo",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=DEMO_REPO, help=f"demo repository (default {DEMO_REPO})")
    parser.add_argument(
        "--baseline", type=Path, default=BASELINE, help=f"baseline folder (default {BASELINE})"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="check everything the live demo needs")
    sub.add_parser("reset", help="put the demo repo back to the baseline (also the first push)")
    merge_cmd = sub.add_parser("merge", help="open and merge a scenario's pull request")
    merge_cmd.add_argument("scenario", choices=sorted(SCENARIOS))
    wait_cmd = sub.add_parser("wait", help="follow what the local server did with a PR")
    wait_cmd.add_argument("pr", type=int)
    wait_cmd.add_argument(
        "--event", default="pull_request", choices=["pull_request", "issue_comment"]
    )
    wait_cmd.add_argument("--timeout", type=int, default=480)
    answer_cmd = sub.add_parser("answer", help="reply to DriftGuard's question as the maintainer")
    answer_cmd.add_argument("pr", type=int)
    answer_cmd.add_argument("--scenario", default="minimum-deposit", choices=sorted(SCENARIOS))
    answer_cmd.add_argument("--text", help="override the scenario's scripted reply")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    for name in ("httpx", "httpx2"):  # the Anthropic SDK logs under its own httpx name
        logging.getLogger(name).setLevel(logging.WARNING)  # the checklist, not every request
    try:
        if args.command == "preflight":
            sys.exit(0 if preflight(settings, args.repo, args.baseline) else 1)
        if args.command == "wait":
            wait(settings, args.repo, args.pr, args.event, args.timeout)
            return
        if not settings.github_token:
            raise DemoError("GITHUB_TOKEN is empty in .env")
        with GitHubClient(settings.github_token) as client:
            if args.command == "reset":
                done = reset(client, args.repo, args.baseline)
                print("\n".join(done) if done else "already at the baseline, nothing to do")
            elif args.command == "merge":
                number, url = merge(client, args.repo, SCENARIOS[args.scenario])
                print(f"PR #{number} merged: {url}")
                print(f"next: python -m driftguard.cli.demo wait {number}")
            elif args.command == "answer":
                text = args.text or SCENARIOS[args.scenario].answer
                if not text:
                    raise DemoError(f"scenario {args.scenario!r} has no scripted reply; use --text")
                client.create_issue_comment(args.repo, args.pr, text)
                print(f"posted on PR #{args.pr}: {text}")
    except (DemoError, GitHubError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
