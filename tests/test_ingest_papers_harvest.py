"""Network-mocked tests for the papers harvest (dry-run path only).

Uses ``respx`` so no test touches the live network. The assertions that
matter operationally: a failed upstream yields zero docs and a recorded
error (never an exception that aborts a batch), stubs are counted and
excluded, and the report never claims a write happened.
"""

from __future__ import annotations

import httpx
import respx

from rugbrain.ingest_sources import papers

TREE_URL = "https://api.github.com/repos/ethereum/EIPs/git/trees/master?recursive=1"
LLAMA_URL = "https://api.llama.fi/protocols"
RAW = "https://raw.githubusercontent.com/ethereum/EIPs/master/"

REAL_EIP = "---\neip: 4844\ntitle: Sharding\nstatus: Draft\n---\n\n" + ("spec body. " * 60)
STUB_EIP = "---\neip: 20\nstatus: Moved\n---\n\nThis file was moved to https://x/ercs\n"
TREE = {
    "tree": [
        {"type": "blob", "path": "EIPS/eip-4844.md", "size": 500},
        {"type": "blob", "path": "EIPS/eip-20.md", "size": 120},
        {"type": "blob", "path": "README.md", "size": 10},
    ]
}
PROTOCOLS = [
    {"name": "Uniswap V3", "slug": "uniswap-v3", "category": "Dexes", "address": "0xabc"},
    {"name": "Aave", "slug": "aave", "category": "Lending", "address": None},
]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=5.0)


@respx.mock
async def test_harvest_separates_substantive_from_stubs() -> None:
    respx.get(TREE_URL).mock(return_value=httpx.Response(200, json=TREE))
    respx.get(RAW + "EIPS/eip-4844.md").mock(return_value=httpx.Response(200, text=REAL_EIP))
    respx.get(RAW + "EIPS/eip-20.md").mock(return_value=httpx.Response(200, text=STUB_EIP))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(200, json=PROTOCOLS))

    async with _client() as c:
        h = await papers.harvest(c, limit=None)

    assert h.stubs == 1
    assert h.substantive == 3  # 1 real EIP + 2 protocols
    assert not h.errors
    assert {d.source for d in h.docs} == {"eips", "defillama"}
    # README.md is not an EIP path: never fetched, never counted.
    # 2 EIP paths + 2 protocol rows.
    assert h.seen == 4


@respx.mock
async def test_point_ids_are_unique_across_docs() -> None:
    respx.get(TREE_URL).mock(return_value=httpx.Response(200, json=TREE))
    respx.get(RAW + "EIPS/eip-4844.md").mock(return_value=httpx.Response(200, text=REAL_EIP))
    respx.get(RAW + "EIPS/eip-20.md").mock(return_value=httpx.Response(200, text=STUB_EIP))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(200, json=PROTOCOLS))

    async with _client() as c:
        h = await papers.harvest(c, limit=None)
    ids = [d.point_id for d in h.docs]
    assert len(ids) == len(set(ids))


@respx.mock
async def test_rerun_produces_identical_ids() -> None:
    """The upsert guarantee: same upstream input, same point ids."""
    respx.get(TREE_URL).mock(return_value=httpx.Response(200, json=TREE))
    respx.get(RAW + "EIPS/eip-4844.md").mock(return_value=httpx.Response(200, text=REAL_EIP))
    respx.get(RAW + "EIPS/eip-20.md").mock(return_value=httpx.Response(200, text=STUB_EIP))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(200, json=PROTOCOLS))

    async with _client() as c:
        first = await papers.harvest(c, limit=None)
    async with _client() as c:
        second = await papers.harvest(c, limit=None)
    assert [d.point_id for d in first.docs] == [d.point_id for d in second.docs]


@respx.mock
async def test_dead_tree_records_error_not_exception() -> None:
    """A dead source must fail open with a recorded error, never raise."""
    respx.get(TREE_URL).mock(return_value=httpx.Response(404))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(200, json=PROTOCOLS))

    async with _client() as c:
        h = await papers.harvest(c, limit=None)
    assert h.substantive == 2
    assert any("404" in e for e in h.errors)


@respx.mock
async def test_dead_defillama_records_error() -> None:
    respx.get(TREE_URL).mock(return_value=httpx.Response(200, json=TREE))
    respx.get(RAW + "EIPS/eip-4844.md").mock(return_value=httpx.Response(200, text=REAL_EIP))
    respx.get(RAW + "EIPS/eip-20.md").mock(return_value=httpx.Response(200, text=STUB_EIP))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(503))

    async with _client() as c:
        h = await papers.harvest(c, limit=None)
    assert h.substantive == 1
    assert any("503" in e for e in h.errors)


@respx.mock
async def test_raw_miss_is_recorded_and_skipped() -> None:
    respx.get(TREE_URL).mock(return_value=httpx.Response(200, json=TREE))
    respx.get(RAW + "EIPS/eip-4844.md").mock(return_value=httpx.Response(404))
    respx.get(RAW + "EIPS/eip-20.md").mock(return_value=httpx.Response(200, text=STUB_EIP))
    respx.get(LLAMA_URL).mock(return_value=httpx.Response(200, json=PROTOCOLS))

    async with _client() as c:
        h = await papers.harvest(c, limit=None)
    assert h.substantive == 2  # only the two protocols
    assert any("404" in e for e in h.errors)


def test_report_declares_dry_run_and_lane() -> None:
    h = papers.Harvest(seen=10, stubs=3)
    text = papers.report(h)
    assert "DRY RUN" in text
    assert "papers_standards" in text
    assert "qwen3-embedding-8b:4096" in text
    assert "nothing written" in text
