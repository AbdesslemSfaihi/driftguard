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


# ---------- classes and methods (perimeter v1.1) ----------


def test_method_default_change_is_detected_with_dotted_symbol():
    old = "class Client:\n    def connect(self, timeout=10):\n        ...\n"
    new = "class Client:\n    def connect(self, timeout=30):\n        ...\n"
    result = facts(old, new)
    assert len(result) == 1
    assert result[0].kind == "default_changed"
    assert result[0].symbol == "Client.connect.timeout"
    assert (result[0].old, result[0].new) == ("10", "30")


def test_self_is_not_a_parameter():
    old = "class Client:\n    def ping(self):\n        return 1\n"
    new = "class Client:\n    def ping(self):\n        return 2\n"
    assert facts(old, new) == []  # body change only; self never counts


def test_constructor_maps_to_the_class_name():
    old = "class Pool:\n    def __init__(self, host, size=8):\n        ...\n"
    new = "class Pool:\n    def __init__(self, host, size=4):\n        ...\n"
    result = facts(old, new)
    assert [(f.kind, f.symbol) for f in result] == [("default_changed", "Pool.size")]


def test_removed_class_is_one_fact_not_one_per_method():
    old = (
        "class Pool:\n"
        "    def __init__(self, host):\n        ...\n"
        "    def acquire(self):\n        ...\n"
        "    def release(self, conn):\n        ...\n"
    )
    result = facts(old, "")
    assert [(f.kind, f.symbol) for f in result] == [("removed", "Pool")]


def test_private_methods_are_not_documented_api():
    old = "class Pool:\n    def _refill(self, count=1):\n        ...\n"
    new = "class Pool:\n    def _refill(self, count=2):\n        ...\n"
    assert facts(old, new) == []


# ---------- module constants (perimeter v1.1) ----------


def test_constant_value_change_is_detected():
    result = facts("POOL_TIMEOUT = 15\n", "POOL_TIMEOUT = 30\n")
    assert len(result) == 1
    fact = result[0]
    assert fact.kind == "constant_changed"
    assert fact.symbol == "POOL_TIMEOUT"
    assert (fact.old, fact.new) == ("15", "30")


def test_removed_constant_is_detected():
    result = facts("MAX_POOL_SIZE = 8\n", "")
    assert [(f.kind, f.symbol) for f in result] == [("removed", "MAX_POOL_SIZE")]


def test_lowercase_module_variables_are_internals_not_constants():
    assert facts("_retry_budget = 3\nlog = None\n", "_retry_budget = 5\nlog = 1\n") == []


def test_annotated_constant_change_is_detected():
    result = facts("TTL: int = 3600\n", "TTL: int = 7200\n")
    assert [(f.kind, f.symbol, f.old, f.new) for f in result] == [
        ("constant_changed", "TTL", "3600", "7200")
    ]
