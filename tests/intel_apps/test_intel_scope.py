"""Rule: these providers are never used for OUR repos — zoekt/serena own that."""
import pytest

from rugbrain.intel_apps import code_intel_facts, grep_github, web_cited_search
from rugbrain.intel_apps.guard import own_repo_refusal

OURS = [
    "RugMunchMedia/rugbrain",
    "rugmunch/pryscraper",
    "rugbrain",
    "internal/rmi-backend",
    "internal-lessons/scanners",
    "docs:fastapi",
]
THEIRS = ["encode/httpx", "pydantic/pydantic", "qdrant/qdrant-client"]


@pytest.mark.parametrize("target", OURS)
def test_our_repos_refused(target):
    out = own_repo_refusal(target, provider="grepapp", tool="searchGitHub")
    assert out is not None
    assert out["status"] == "refused"
    assert out["error"]["code"] == "own_repo_out_of_scope"
    assert "zoekt" in out["error"]["reason"]


@pytest.mark.parametrize("target", THEIRS)
def test_third_party_allowed(target):
    assert own_repo_refusal(target, provider="grepapp", tool="searchGitHub") is None


async def test_adapters_never_call_transport_for_our_repos(recorder):
    inv = recorder({"should": "never be used"})
    a = await code_intel_facts(inv, owner="RugMunchMedia", repo="rugbrain")
    b = await grep_github(inv, "def ", repo="RugMunchMedia/rugbrain")
    c = await web_cited_search(inv, "internal/rmi-backend scanners")
    assert [a["status"], b["status"], c["status"]] == ["refused"] * 3
    assert inv.calls == []
