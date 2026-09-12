"""Drive the live ask-the-developer round trip on the demo repository.

    python scripts/demo/live_round_trip.py merge             # merge a PR: deliver() attempts 3 -> 5
    python scripts/demo/live_round_trip.py wait 7            # what the local server did with PR #7
    python scripts/demo/live_round_trip.py answer 7          # reply "Q1: yes ..." as the repo owner
    python scripts/demo/live_round_trip.py wait 7 --event issue_comment

The merged change has the shape of benchmark case 25: the webhooks page names
deliver() and must be fixed; the FAQ's unattributed "3 attempts in total by
default" could describe request() too and must become a question, not an edit.
Needs the webhook server and the smee relay running and, for the answer half,
the demo webhook subscribed to Issue comments.
"""

import argparse
import base64
import sqlite3
import sys
import time

import httpx

from driftguard.config import get_settings

REPO = "AbdesslemSfaihi/pyconnect-demo"
API = "https://api.github.com"
FINAL_STATUSES = {
    "no_drift",
    "docs_pr_opened",
    "proposals_ready",
    "all_rejected",
    "questions_open",
    "error",
    "agent_error",
    "logged",
    "answers_processed",
    "no_answers_matched",
}


def github() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )


def checked(response: httpx.Response, what: str) -> dict:
    if response.status_code >= 400:
        sys.exit(f"{what}: {response.status_code} {response.text[:300]}")
    return response.json()


def merge(_: argparse.Namespace) -> None:
    branch = "raise-deliver-attempts"
    old, new = "def deliver(event, url, attempts=3):", "def deliver(event, url, attempts=5):"
    with github() as gh:
        ref = checked(gh.get(f"/repos/{REPO}/git/ref/heads/main"), "main ref")
        main_sha = ref["object"]["sha"]
        current = checked(
            gh.get(f"/repos/{REPO}/contents/src/pyconnect/webhooks.py", params={"ref": "main"}),
            "read webhooks.py",
        )
        text = base64.b64decode(current["content"]).decode()
        if text.count(old) != 1:
            sys.exit("deliver() signature not found exactly once on main — already merged?")
        ref_body = {"ref": f"refs/heads/{branch}", "sha": main_sha}
        checked(gh.post(f"/repos/{REPO}/git/refs", json=ref_body), "create branch")
        commit_body = {
            "message": "feat: raise default webhook delivery attempts to 5",
            "content": base64.b64encode(text.replace(old, new).encode()).decode(),
            "branch": branch,
            "sha": current["sha"],
        }
        contents_url = f"/repos/{REPO}/contents/src/pyconnect/webhooks.py"
        checked(gh.put(contents_url, json=commit_body), "commit")
        pr_body = {
            "title": "feat: raise default webhook delivery attempts to 5",
            "head": branch,
            "base": "main",
            "body": "deliver() now tries 5 times by default (was 3).",
        }
        pr = checked(gh.post(f"/repos/{REPO}/pulls", json=pr_body), "open PR")
        merge_url = f"/repos/{REPO}/pulls/{pr['number']}/merge"
        merged = checked(gh.put(merge_url, json={"merge_method": "merge"}), "merge")
        print(f"PR #{pr['number']} merged: {pr['html_url']} (merge commit {merged.get('sha')})")


def wait(args: argparse.Namespace) -> None:
    settings = get_settings()
    deadline = time.time() + args.timeout
    row = None
    while time.time() < deadline:
        con = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM deliveries WHERE repo = ? AND pr_number = ? AND event = ?"
            " ORDER BY received_at DESC LIMIT 1",
            (REPO, args.pr, args.event),
        ).fetchone()
        if row and row["status"] in FINAL_STATUSES:
            break
        con.close()
        time.sleep(5)
    if row is None:
        sys.exit(
            "no delivery arrived — is smee forwarding, and is the webhook subscribed to this event?"
        )
    print(f"delivery {row['delivery_id']}  status={row['status']}  docs_pr={row['docs_pr_url']}")
    questions = con.execute(
        "SELECT q_label, status, doc_path, heading, question, answer FROM questions"
        " WHERE repo = ? AND pr_number = ? ORDER BY id",
        (REPO, args.pr),
    )
    for q in questions:
        print(f"  {q['q_label']} [{q['status']}] {q['doc_path']} :: {q['heading']}")
        print(f"     {q['question']}" + (f"  -> answer: {q['answer']}" if q["answer"] else ""))
    proposals = con.execute(
        "SELECT doc_path, heading, status, facts FROM proposals WHERE delivery_id = ?",
        (row["delivery_id"],),
    )
    for p in proposals:
        print(f"  proposal [{p['status']}] {p['doc_path']} :: {p['heading']}  ({p['facts']})")


def answer(args: argparse.Namespace) -> None:
    with github() as gh:
        url = f"/repos/{REPO}/issues/{args.pr}/comments"
        comment = checked(gh.post(url, json={"body": args.text}), "comment")
        print("posted:", comment["html_url"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("merge", help="open and merge the attempts 3 -> 5 PR").set_defaults(run=merge)
    wait_cmd = sub.add_parser("wait", help="wait for the local server to process a delivery")
    wait_cmd.add_argument("pr", type=int)
    wait_cmd.add_argument(
        "--event", default="pull_request", choices=["pull_request", "issue_comment"]
    )
    wait_cmd.add_argument("--timeout", type=int, default=480)
    wait_cmd.set_defaults(run=wait)
    answer_cmd = sub.add_parser("answer", help="reply to DriftGuard's question as the owner")
    answer_cmd.add_argument("pr", type=int)
    answer_cmd.add_argument("--text", default="Q1: yes, that FAQ answer is about deliver()")
    answer_cmd.set_defaults(run=answer)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
