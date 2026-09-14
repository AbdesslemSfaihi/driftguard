"""Send, or just print, the notification event for an archived delivery.

    python -m driftguard.cli.notify --latest --print
    python -m driftguard.cli.notify --latest
    python -m driftguard.cli.notify <delivery_id> --url http://localhost:5678/webhook-test/driftguard

Why this exists: n8n's editor listens for ONE test event while you build a
workflow, on a test URL that differs from the production one. This replays a
real, archived run into it without merging anything — and --print is the
quickest way to read the exact contract a consumer receives.
"""

import argparse
import json
import logging
import sys

from driftguard.config import get_settings
from driftguard.notify import build_event, send
from driftguard.storage import db


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="driftguard-notify", description=__doc__)
    parser.add_argument("delivery_id", nargs="?", help="an archived delivery id")
    parser.add_argument("--latest", action="store_true", help="use the most recent delivery")
    parser.add_argument("--print", action="store_true", help="print the event, do not send")
    parser.add_argument("--url", help="send here instead of NOTIFY_WEBHOOK_URL")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    delivery_id = args.delivery_id
    if args.latest:
        rows = db.list_deliveries(settings.database_path, limit=1)
        if not rows:
            sys.exit("no deliveries archived yet")
        delivery_id = rows[0]["delivery_id"]
    if not delivery_id:
        parser.error("give a delivery id or --latest")

    event = build_event(settings, delivery_id)
    if event is None:
        sys.exit(f"unknown delivery {delivery_id}")
    if args.print:
        print(json.dumps(event, indent=2, ensure_ascii=False))
        return
    if not (args.url or settings.notify_webhook_url):
        sys.exit("nowhere to send: set NOTIFY_WEBHOOK_URL in .env or pass --url")
    ok = send(settings, event, url=args.url)
    print(f"{'sent' if ok else 'NOT delivered'}: {event['summary']}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
