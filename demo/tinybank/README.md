# TinyBank

A deliberately small banking library. It exists to show what
[DriftGuard](https://github.com/AbdesslemSfaihi/driftguard) does, on an
example anyone can follow.

The bank's rules live in two places, on purpose:

| Where | What it is | Who reads it |
|---|---|---|
| [docs/account-rules.md](docs/account-rules.md) | the handbook: the rules in plain language | customers, support, product |
| [src/tinybank/accounts.py](src/tinybank/accounts.py) | the code: the same rules, enforced | developers |

The two must say the same thing. In real projects they slowly stop doing so:
the code changes, the handbook does not. DriftGuard watches every merged pull
request on this repository, finds the handbook sections the change made wrong,
and opens a pull request that fixes them, with the evidence attached. When a
section does not say which rule it describes, DriftGuard asks the maintainer
instead of guessing.

Other pages: [Getting started](docs/getting-started.md) for developers,
the [FAQ](docs/faq.md) for customers, and the [changelog](docs/changelog.md).

```bash
pip install -e .
pytest
```
