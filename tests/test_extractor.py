"""Exercise 01 contract — make these pass, one at a time.

Delete the `pytestmark` line below to activate them, then:
    pytest tests/test_extractor.py -x
"""

import pytest

from driftguard.analysis.extractor import DriftFact, extract_drift_facts

pytestmark = pytest.mark.skip(reason="Exercise 01: delete this line and implement the extractor")


def facts(old: str, new: str) -> list[DriftFact]:
    return extract_drift_facts(old, new, "demo/client.py")


def test_unchanged_file_produces_no_facts():
    src = "def connect(host, timeout=10):\n    return host\n"
    assert facts(src, src) == []


def test_removed_function_is_detected():
    old = "def connect(host):\n    ...\n\ndef legacy_mode():\n    ...\n"
    new = "def connect(host):\n    ...\n"
    result = facts(old, new)
    assert len(result) == 1
    fact = result[0]
    assert fact.kind == "removed"
    assert fact.symbol == "legacy_mode"
    assert fact.file == "demo/client.py"


def test_added_required_parameter_changes_signature():
    old = "def connect(host):\n    ...\n"
    new = "def connect(host, api_key):\n    ...\n"
    result = facts(old, new)
    assert len(result) == 1
    assert result[0].kind == "signature_changed"
    assert result[0].symbol == "connect"


def test_changed_default_is_detected_with_old_and_new_values():
    old = "def connect(host, timeout=10):\n    ...\n"
    new = "def connect(host, timeout=30):\n    ...\n"
    result = facts(old, new)
    assert len(result) == 1
    fact = result[0]
    assert fact.kind == "default_changed"
    assert fact.symbol == "connect.timeout"
    assert fact.old == "10"
    assert fact.new == "30"


def test_renamed_parameter_same_position_is_detected():
    old = "def connect(host, timeout=10):\n    ...\n"
    new = "def connect(host, request_timeout=10):\n    ...\n"
    result = facts(old, new)
    assert len(result) == 1
    fact = result[0]
    assert fact.kind == "renamed"
    assert fact.old == "timeout"
    assert fact.new == "request_timeout"


def test_rename_plus_default_change_emits_both_facts():
    # The exact scenario of the supervisor demo:
    # timeout=10  ->  request_timeout=30
    old = "def connect(host, timeout=10):\n    ...\n"
    new = "def connect(host, request_timeout=30):\n    ...\n"
    result = facts(old, new)
    kinds = sorted(fact.kind for fact in result)
    assert kinds == ["default_changed", "renamed"]
