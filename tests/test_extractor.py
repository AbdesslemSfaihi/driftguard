"""Contract for the drift-fact extractor (v1 perimeter: module-level functions)."""

from driftguard.analysis.extractor import DriftFact, extract_drift_facts


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


def test_swapped_parameters_are_one_signature_change_not_two_renames():
    old = "def connect(host, port):\n    ...\n"
    new = "def connect(port, host):\n    ...\n"
    result = facts(old, new)
    assert [fact.kind for fact in result] == ["signature_changed"]
    assert result[0].old == "(host, port)"
    assert result[0].new == "(port, host)"


def test_rename_colliding_with_a_surviving_name_is_a_signature_change():
    # a -> b while b already existed: positional "renames" would tell a false
    # story (a renamed to b AND b renamed to c), so one honest fact instead.
    old = "def connect(a, b):\n    ...\n"
    new = "def connect(b, c):\n    ...\n"
    result = facts(old, new)
    assert [fact.kind for fact in result] == ["signature_changed"]


def test_unparseable_source_produces_no_facts_instead_of_crashing():
    # A false accusation costs developer trust (precision >= 90% is a v1 metric),
    # so a file we cannot parse yields nothing rather than guesses.
    old = "def connect(host):\n    ...\n"
    broken = "def connect(host:\n"
    assert facts(old, broken) == []
    assert facts(broken, old) == []


def test_methods_inside_classes_are_ignored_in_v1():
    old = "class Client:\n    def connect(self, timeout=10):\n        ...\n"
    new = "class Client:\n    def connect(self, timeout=30):\n        ...\n"
    assert facts(old, new) == []
