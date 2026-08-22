"""Replay archived webhook deliveries through the agent, no GitHub needed.

    python -m driftguard.cli.replay list
    python -m driftguard.cli.replay run <delivery_id>

Why this exists: webhooks are painful to reproduce (you cannot re-merge a PR).
Since the gate archives every raw payload, any past event can be re-run at will —
that gives us deterministic integration tests, easy debugging, and an offline
fallback for the live demo.
"""

import argparse
import logging
import sys

from driftguard.agent.handler import process_delivery
from driftguard.config import get_settings
from driftguard.storage import db


def main() -> None:
    # PR titles and payloads may contain any unicode; Windows consoles default
    # to a legacy codepage, so force UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="driftguard-replay", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="show archived deliveries")
    list_cmd.add_argument("--limit", type=int, default=20)

    run_cmd = sub.add_parser("run", help="re-process one archived delivery")
    run_cmd.add_argument("delivery_id")

    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if args.command == "list":
        rows = db.list_deliveries(settings.database_path, limit=args.limit)
        if not rows:
            print("no deliveries archived yet")
            return
        for row in rows:
            print(
                f"{row['received_at']}  {row['delivery_id']}  "
                f"{row['repo']}#{row['pr_number']}  [{row['status']}]"
            )
    elif args.command == "run":
        process_delivery(settings, args.delivery_id)


if __name__ == "__main__":
    main()
