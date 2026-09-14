# Notifications with n8n

DriftGuard sends **one JSON event at the end of every run** to a webhook URL.
n8n receives it, looks at `severity`, and tells the right people through the
right channel. DriftGuard states what happened; n8n decides who hears about it.

```
GitHub ──webhook──▶ DriftGuard ──run──▶ docs PR / question on the PR
                        │
                        └──▶ POST event ──▶ n8n Webhook ──▶ Switch(severity)
                                                              ├─ action ─▶ "please review / please answer"
                                                              ├─ alert  ─▶ "the agent failed, replay it"
                                                              └─ info   ─▶ log only (or nothing)
```

## The event

```json
{
  "event": "driftguard.run.finished",
  "sent_at": "2026-09-14T09:12:03+00:00",
  "delivery_id": "b4d9baa0-aece-11f1-8eeb-233b771281ea",
  "kind": "merged_pr",
  "status": "docs_pr_opened",
  "severity": "action",
  "repo": "AbdesslemSfaihi/pyconnect-demo",
  "pr_number": 10,
  "pr_title": "feat: raise default webhook delivery attempts to 7",
  "pr_url": "https://github.com/AbdesslemSfaihi/pyconnect-demo/pull/10",
  "author": "AbdesslemSfaihi",
  "docs_pr_url": "https://github.com/AbdesslemSfaihi/pyconnect-demo/pull/11",
  "docs_pr_number": 11,
  "story_url": "http://localhost:8000/ui/deliveries/b4d9baa0-aece-11f1-8eeb-233b771281ea",
  "facts": [{"kind": "default_changed", "symbol": "deliver.attempts", "old": "5", "new": "7", "file": "src/pyconnect/webhooks.py"}],
  "proposals": {"published": 1, "verified": 0, "rejected": 0, "stale": 0},
  "questions": [{"label": "Q1", "status": "open", "doc_path": "docs/faq.md", "heading": "FAQ > How many times is a failed send retried?", "question": "Is this FAQ answer about deliver()'s attempts default?", "answer": null, "answered_by": null, "comment_url": "https://github.com/AbdesslemSfaihi/pyconnect-demo/pull/10#issuecomment-5647125203"}],
  "open_questions": 1,
  "summary": "PR #10 on AbdesslemSfaihi/pyconnect-demo: 1 drift fact(s) · docs PR #11 opened for review · 1 question(s) waiting for a maintainer (Q1)"
}
```

| field | meaning |
|---|---|
| `kind` | `merged_pr` (a code PR was merged) or `answer` (a maintainer replied to a question) |
| `status` | the delivery's final status: `docs_pr_opened`, `questions_open`, `proposals_ready`, `no_drift`, `all_rejected`, `answers_processed`, `no_answers_matched`, `logged`, `error`, `agent_error` |
| `severity` | `action` (a human should review or answer), `alert` (the run failed or every fix was rejected), `info` (nothing to do) |
| `summary` | one sentence that stands on its own; use it as the message body |
| `story_url` | the story page for this PR, built from `PUBLIC_BASE_URL` |

Every request carries the header `X-DriftGuard-Token: <NOTIFY_WEBHOOK_TOKEN>`.
Requests time out after 5 seconds and are never retried: the archive is the
source of truth, the message is a courtesy.

## Part 1 — start n8n

Pick one.

**Local, with Node** (Node is already installed on this machine):

```bash
npx --yes n8n
```

Then open http://localhost:5678 and create the owner account (local only).
If n8n refuses your Node version, use Docker instead:

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
```

**n8n Cloud** works too: DriftGuard pushes to n8n, so a cloud URL is fine.
Only the `story_url` links point at your machine.

## Part 2 — the token

DriftGuard already has a token in `.env` (`NOTIFY_WEBHOOK_TOKEN`). Read it, you
will paste it into n8n in step 4:

```bash
grep NOTIFY_WEBHOOK_TOKEN .env
```

## Part 3 — build the workflow in the n8n editor

1. **Create a workflow.** Top left, *Create workflow*. Name it
   `DriftGuard notifications` (click the title to rename).

2. **Add the trigger.** Click *Add first step*, search **Webhook**, select it.
   In the node panel set:
   - *HTTP Method*: `POST`
   - *Path*: `driftguard`
   - *Authentication*: `Header Auth`
   - *Respond*: `Immediately`

3. **Create the credential** (still in the Webhook node): next to
   *Credential for Header Auth* choose *Create new credential*. Fill:
   - *Name*: `X-DriftGuard-Token`
   - *Value*: the token from Part 2
   Save. This is what makes n8n reject any request that does not carry your token.

4. **Note the two URLs** at the top of the node panel. *Test URL* looks like
   `http://localhost:5678/webhook-test/driftguard` and only works while the
   editor is listening. *Production URL* looks like
   `http://localhost:5678/webhook/driftguard` and works once the workflow is
   active. Copy the **Production URL**.

5. **Point DriftGuard at n8n.** In `.env`, set
   `NOTIFY_WEBHOOK_URL=http://localhost:5678/webhook/driftguard` (the
   Production URL), keep `NOTIFY_WEBHOOK_TOKEN` as it is, then restart the
   server:

   ```bash
   uvicorn driftguard.api.webhook:app --port 8000
   ```

6. **Feed the editor one real event.** In the Webhook node click
   *Listen for test event*. In a terminal, replay the latest archived run into
   the **Test URL**:

   ```bash
   python -m driftguard.cli.notify --latest --url http://localhost:5678/webhook-test/driftguard
   ```

   The node now shows the event under *Output*, with the fields under `body`.
   Everything below uses `{{ $json.body.… }}` because the Webhook node wraps
   the request.

7. **Add the router.** Click the `+` after the Webhook node, search **Switch**.
   - *Mode*: `Rules`
   - Rule 1: left value `{{ $json.body.severity }}`, operator *is equal to*,
     right value `action`. Turn on *Rename Output* and call it `action`.
   - *Add Routing Rule* → Rule 2: same left value, equals `alert`, output `alert`.
   - Open *Options* → *Fallback Output* → `Extra Output`, name it `info`.

   The node now has three outputs: action, alert, info.

8. **Add the messages.** Choose one channel per output. Suggested:
   - **action** → a Discord or Slack message to the maintainers.
   - **alert** → the same channel with an alert prefix, or an e-mail to you.
   - **info** → leave unconnected, or a **No Operation** node. Every merged PR
     produces an `info` event when nothing drifted; nobody needs a ping for that.

   **Discord** (fastest, no app to register): in your Discord server, *Server
   Settings → Integrations → Webhooks → New Webhook*, copy the webhook URL.
   In n8n add a **Discord** node on the `action` output: *Connection Type*
   `Webhook`, create a credential with that URL, *Operation* `Send a Message`,
   and put this in *Message* (switch the field to *Expression*):

   ```
   {{ $json.body.summary }}
   Story: {{ $json.body.story_url }}
   {{ $json.body.docs_pr_url ? 'Review: ' + $json.body.docs_pr_url : 'Answer on: ' + $json.body.pr_url }}
   ```

   **Slack** (incoming webhook): create an incoming webhook in your Slack
   workspace, add an **HTTP Request** node: *Method* `POST`, *URL* the Slack
   webhook URL, *Send Body* on, *Body Content Type* `JSON`, *Specify Body*
   `Using JSON`, body:

   ```
   {{ { "text": $json.body.summary + "\n" + $json.body.story_url } }}
   ```

   For the `alert` output, duplicate the node and start the message with
   `:rotating_light: ` or `⚠ `.

9. **Run it once from the editor.** Click *Test workflow*. The event you sent
   in step 6 flows through the Switch to the `action` branch and the message
   arrives in your channel.

10. **Activate.** Toggle *Inactive* → *Active* at the top right. From now on
    the Production URL is live and every DriftGuard run produces one message
    on the right branch.

## Part 4 — prove it end to end

With the server, the smee relay and n8n running:

```bash
python scripts/demo/live_round_trip.py merge
```

Within a minute: a docs PR and a question on GitHub, a new story on
http://localhost:8000/ui, an execution in n8n's *Executions* tab, and one
message in your channel. Reply to the question on the PR and a second message
announces the follow-up PR.

## Troubleshooting

- **n8n execution shows 403 / "Authorization data is wrong"**: the token in the
  n8n credential differs from `NOTIFY_WEBHOOK_TOKEN` in `.env`.
- **Nothing arrives while testing**: you are sending to the Production URL
  while the editor listens on the Test URL, or the workflow is not active.
  *Listen for test event* catches exactly one event; click it again.
- **DriftGuard logs `notification … not delivered`**: n8n is not running, or
  the URL in `.env` is wrong. The run itself is unaffected.
- **Story links do not open on another machine**: they are built from
  `PUBLIC_BASE_URL`, which defaults to `http://localhost:8000`.
