"""Happy paths: each provider returns the one typed envelope shape."""
from rugbrain.intel_apps import code_intel_facts, grep_github, web_cited_search

KEYS = {"status", "provider", "tool", "data", "error", "latency_ms", "fetched_at"}


async def test_repo_facts_ok(fixture, recorder):
    out = await code_intel_facts(
        recorder(fixture("code_intel_repo_httpx")), owner="encode", repo="httpx"
    )
    assert set(out) == KEYS
    assert out["status"] == "ok"
    assert out["provider"] == "code-intel"
    assert out["tool"] == "repo_facts"
    assert out["data"]["scorecard"]["score"] == 4.9
    assert isinstance(out["latency_ms"], int)


async def test_package_facts_ok(fixture, recorder):
    out = await code_intel_facts(
        recorder(fixture("code_intel_package_httpx")), system="pypi", name="httpx"
    )
    assert out["tool"] == "package_facts"
    assert out["data"]["dependencies"]["dependency_count"] == 2


async def test_grep_github_passes_filters(fixture, recorder):
    inv = recorder(fixture("grepapp_httpx"))
    out = await grep_github(inv, "httpx.AsyncClient(", language=["Python"])
    assert out["status"] == "ok"
    assert inv.calls == [("searchGitHub", {"query": "httpx.AsyncClient(", "language": ["Python"]})]


async def test_cited_search_ok_and_clamps_limit(fixture, recorder):
    inv = recorder(fixture("searxng_contract"))
    out = await web_cited_search(inv, "ethereum fusaka", limit=99)
    assert out["status"] == "ok"
    assert inv.calls[0][1]["limit"] == 10
