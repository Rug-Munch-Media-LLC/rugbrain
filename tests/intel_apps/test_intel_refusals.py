"""Every provider refuses loudly and names itself — no silent failures."""
import pytest

from rugbrain.intel_apps import code_intel_facts, grep_github, web_cited_search

ADAPTERS = {
    "code-intel": lambda inv, **kw: code_intel_facts(inv, owner="encode", repo="httpx", **kw),
    "grepapp": lambda inv, **kw: grep_github(inv, "httpx.AsyncClient(", **kw),
    "searxng": lambda inv, **kw: web_cited_search(inv, "ethereum gas price", **kw),
}


@pytest.mark.parametrize("provider", ADAPTERS)
async def test_timeout_refusal(provider, recorder):
    out = await ADAPTERS[provider](recorder(None, seconds=0.05), timeout_s=0.01)
    assert out["status"] == "refused"
    assert out["error"]["code"] == "timeout"
    assert provider in out["error"]["reason"]


@pytest.mark.parametrize("provider", ADAPTERS)
async def test_transport_error_refusal(provider, recorder):
    out = await ADAPTERS[provider](recorder(raises=RuntimeError("boom")))
    assert out["status"] == "refused"
    assert out["error"]["code"] == "provider_error"
    assert provider in out["error"]["reason"]


@pytest.mark.parametrize("provider", ADAPTERS)
async def test_empty_result_refusal(provider, recorder):
    out = await ADAPTERS[provider](recorder({}))
    assert out["error"]["code"] == "empty_result"


async def test_searxng_upstream_error_payload_is_refused(fixture, recorder):
    """The real 2026-10-25 searxng answer when :8088 was down."""
    out = await web_cited_search(recorder(fixture("searxng_error")), "ethereum gas price")
    assert out["status"] == "refused"
    assert out["error"]["code"] == "upstream_error"
    assert "Connection refused" in out["error"]["reason"]
    assert out["data"] is None


def test_refusal_requires_naming_the_provider():
    from rugbrain.intel_apps.envelope import envelope

    with pytest.raises(ValueError):
        envelope(provider="searxng", tool="cited_search", status="refused", code="timeout", reason="deadline hit")
