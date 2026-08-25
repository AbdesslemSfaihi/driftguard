from driftguard.analysis.extractor import DriftFact
from driftguard.analysis.queries import queries_for_fact


def test_renamed_queries_lead_with_the_old_name():
    fact = DriftFact("renamed", "connect.timeout", old="timeout", new="request_timeout",
                     file="a.py")
    queries = queries_for_fact(fact)
    assert queries[0] == "timeout"  # the docs still say the OLD name
    assert any("connect" in q for q in queries)


def test_default_changed_queries_mention_param_and_old_value():
    fact = DriftFact("default_changed", "connect.timeout", old="10", new="30", file="a.py")
    queries = queries_for_fact(fact)
    assert any("timeout" in q and "10" in q for q in queries)
    assert any("default" in q for q in queries)


def test_removed_queries_use_the_old_symbol():
    fact = DriftFact("removed", "legacy_login", old="legacy_login", new=None, file="a.py")
    queries = queries_for_fact(fact)
    assert queries[0] == "legacy_login"


def test_dotted_method_symbols_query_by_short_names():
    fact = DriftFact("renamed", "ConnectionPool.acquire.wait", "wait", "block", "pool.py")
    queries = queries_for_fact(fact)
    assert queries[0] == "wait"  # docs say acquire(wait=...), not the dotted path
    assert "acquire wait" in queries


def test_constant_changed_queries_lead_with_the_constant_name():
    fact = DriftFact("constant_changed", "POOL_TIMEOUT", "15", "30", "pool.py")
    queries = queries_for_fact(fact)
    assert queries[0] == "POOL_TIMEOUT"
    assert "POOL_TIMEOUT 15" in queries  # the old value is what drifted docs say


def test_removed_method_queries_include_the_bare_method_name():
    fact = DriftFact("removed", "ConnectionPool.drain", "ConnectionPool.drain", None, "pool.py")
    assert "drain" in queries_for_fact(fact)


def test_queries_are_deduplicated_and_non_empty():
    fact = DriftFact("signature_changed", "connect", old="(host)", new="(host, key)",
                     file="a.py")
    queries = queries_for_fact(fact)
    assert queries == list(dict.fromkeys(queries))
    assert all(q.strip() for q in queries)
