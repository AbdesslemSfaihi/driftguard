"""Contract for deterministic fix verification — the referee the LLM can't charm."""

from driftguard.agent.verify import verify_fix
from driftguard.analysis.extractor import DriftFact

RENAME = DriftFact("renamed", "connect.timeout", "timeout", "request_timeout", "client.py")
DEFAULT = DriftFact("default_changed", "connect.timeout", "10", "30", "client.py")
REMOVED = DriftFact("removed", "legacy_login", "legacy_login", None, "auth.py")
SIGNATURE = DriftFact(
    "signature_changed", "request", "(method, url, retries=2)", "(method, url)", "client.py"
)


def test_clean_rename_fix_passes():
    old = "## Timeouts\n\nSet `timeout` to control the wait."
    new = "## Timeouts\n\nSet `request_timeout` to control the wait."
    assert verify_fix([RENAME], old, new) is None


def test_rename_fix_fails_when_old_name_survives():
    old = "Set `timeout` here."
    new = "Set `request_timeout` (was `timeout`) here."
    assert "timeout" in verify_fix([RENAME], old, new)


def test_old_name_inside_the_new_name_is_not_a_false_positive():
    # \btimeout\b must not match inside request_timeout
    new = "Use `request_timeout` everywhere."
    assert verify_fix([RENAME], "Use `timeout`.", new) is None


def test_default_change_requires_new_value_and_forbids_old():
    old = "The default is 10 seconds."
    assert verify_fix([DEFAULT], old, "The default is 30 seconds.") is None
    assert "10" in verify_fix([DEFAULT], old, "The default is 10 seconds. Now 30.")
    assert "30" in verify_fix([DEFAULT], old, "The default changed recently.")


def test_removed_function_must_disappear():
    old = "Call `legacy_login()` first."
    assert "legacy_login" in verify_fix([REMOVED], old, "Call `legacy_login()` if needed.")
    assert verify_fix([REMOVED], old, "Authenticate with `login()`.") is None


def test_signature_change_forbids_showing_removed_parameters():
    old = "```python\nrequest('GET', url, retries=2)\n```"
    still_there = "```python\nrequest('GET', url, retries=5)\n```"
    fixed = "```python\nrequest('GET', url)\n```"
    assert "retries" in verify_fix([SIGNATURE], old, still_there)
    assert verify_fix([SIGNATURE], old, fixed) is None


def test_signature_change_requires_new_parameters_to_appear():
    fact = DriftFact("signature_changed", "connect", "(host)", "(host, api_key)", "client.py")
    old = "Call connect(host)."
    assert "api_key" in verify_fix([fact], old, "Call connect with your host.")
    assert verify_fix([fact], old, "Call connect(host, api_key).") is None


def test_heading_must_be_preserved_at_the_same_level():
    old = "## Timeouts\n\nSet `timeout`."
    no_heading = "Set `request_timeout`."
    wrong_level = "# Timeouts\n\nSet `request_timeout`."
    assert "heading" in verify_fix([RENAME], old, no_heading)
    assert "heading" in verify_fix([RENAME], old, wrong_level)


def test_empty_or_unchanged_rewrites_are_rejected():
    old = "Set `timeout`."
    assert verify_fix([RENAME], old, "   ") == "the rewritten section is empty"
    assert "nothing was changed" in verify_fix([RENAME], old, old)


def test_multiple_facts_are_all_enforced():
    old = "## Timeouts\n\nSet `timeout`. The default is 10."
    half_fixed = "## Timeouts\n\nSet `request_timeout`. The default is 10."
    fully_fixed = "## Timeouts\n\nSet `request_timeout`. The default is 30."
    assert "10" in verify_fix([RENAME, DEFAULT], old, half_fixed)
    assert verify_fix([RENAME, DEFAULT], old, fully_fixed) is None
