def test_classify_query():
    from rmi_rag.search_service import _classify_query
    assert _classify_query("0xabc123") in ("entity_heavy", "keyword_heavy", "balanced")
    assert _classify_query("explain how defi works") == "semantic_heavy"
    assert _classify_query("btc") == "keyword_heavy"

def test_three_pillar_exists():
    import inspect

    from rmi_rag.search_service import three_pillar_search
    sig = inspect.signature(three_pillar_search)
    params = list(sig.parameters.keys())
    assert "query" in params
    assert "collections" in params
