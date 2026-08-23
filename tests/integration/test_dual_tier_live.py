"""LIVE integration tests — dual-tier stack vs RS8000 (netcup Talos) specs.

Runs against real services. Gated: set RUN_LIVE_TESTS=1 to execute.
    RUN_LIVE_TESTS=1 pytest tests/integration/test_dual_tier_live.py -v

Validated against host reality (RS8000, 16c EPYC 9645, 62Gi RAM, CPU-only):
  * exact embedding dimensions per tier (1024 speed / 2560 deep)
  * rerank discrimination on a crypto-forensics pair (instruction-aware)
  * latency budgets measured under production load
  * concurrent-slot behavior (parallel=2 per service)
  * chat generation cleanliness (no <think> leakage) and grounding

Services under test:
  :8003 deep rerank (4B)   :8004 deep embed (4B)
  :8007 speed embed (0.6B) :8008 speed rerank (0.6B)
  :8009 chat (8B instruct)
"""

from __future__ import annotations

import asyncio
import os
import time

import aiohttp
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1",
        reason="live services required: RUN_LIVE_TESTS=1",
    ),
]

BASE = os.getenv("DUAL_TIER_HOST", "http://172.22.0.1")
SPEED_EMBED = f"{BASE}:8007"
SPEED_RERANK = f"{BASE}:8008"
DEEP_EMBED = f"{BASE}:8004"
DEEP_RERANK = f"{BASE}:8003"
CHAT = f"{BASE}:8009"

# RS8000 latency budgets (seconds). Generous: host runs the embed migration
# concurrently; budgets are for PRODUCTION ACCEPTANCE, not best-case.
BUDGET_EMBED_SHORT = 5.0  # single short text, either tier
BUDGET_RERANK_10 = 30.0  # 10 docs, either tier
BUDGET_CHAT_80TOK = 240.0  # 80-token completion, cold-ish cache allowed

RUG_DOCS = [
    (
        "rug",
        "TokenX liquidity analysis: The deployer wallet holds 85% of the "
        "Uniswap LP tokens and retains full mint authority. Ownership is not "
        "renounced, no lockup exists, and a blacklist function can block sells.",
    ),
    (
        "safe",
        "TokenY liquidity analysis: 100% of LP tokens are burned, ownership "
        "renounced, liquidity locked 12 months on Team Finance, 3-of-5 "
        "multisig treasury, 8 months of organic volume.",
    ),
]
RUG_QUERY_INSTRUCTED = (
    "[TASK] Crypto forensics: rank documents by the strength of rug-pull risk "
    "signals they describe, such as concentrated LP ownership and mint authority "
    "[QUERY] Which token shows stronger rug-pull warning signs?"
)


async def _post(session: aiohttp.ClientSession, url: str, payload: dict) -> tuple[float, dict]:
    t0 = time.perf_counter()
    async with session.post(url, json=payload) as resp:
        resp.raise_for_status()
        return round(time.perf_counter() - t0, 3), await resp.json()


# ── health ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_engines_healthy():
    async with aiohttp.ClientSession() as s:
        for url in (SPEED_EMBED, SPEED_RERANK, DEEP_EMBED, DEEP_RERANK, CHAT):
            async with s.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=10)) as r:
                assert r.status == 200, f"{url} unhealthy"
                assert (await r.json()).get("status") == "ok"


# ── embedding dimensions (RS8000 spec) ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,dims,name",
    [
        (SPEED_EMBED, 1024, "speed-0.6B"),
        (DEEP_EMBED, 2560, "deep-4B"),
    ],
)
async def test_embedding_dimensions(url, dims, name):
    async with aiohttp.ClientSession() as s:
        dt, data = await _post(s, f"{url}/v1/embeddings", {"input": "rug pull risk check"})
        assert dt < BUDGET_EMBED_SHORT, f"{name} embed took {dt}s"
        got = len(data["data"][0]["embedding"])
        assert got == dims, f"{name}: expected {dims} dims, got {got}"


@pytest.mark.asyncio
async def test_embed_concurrent_slots():
    """parallel=2 per service: two simultaneous requests must both succeed."""
    async with aiohttp.ClientSession() as s:
        tasks = [
            _post(s, f"{SPEED_EMBED}/v1/embeddings", {"input": f"concurrent probe {i}"})
            for i in range(2)
        ]
        results = await asyncio.gather(*tasks)
        assert all(d["data"][0]["embedding"] for _, d in results)


# ── rerank quality + discrimination ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("url,name", [(SPEED_RERANK, "0.6B"), (DEEP_RERANK, "4B")])
async def test_rerank_discrimination_with_instruction(url, name):
    """THE platform-critical check: rug evidence must outrank healthy pool."""
    async with aiohttp.ClientSession() as s:
        dt, data = await _post(
            s,
            f"{url}/rerank",
            {
                "query": RUG_QUERY_INSTRUCTED,
                "documents": [t for _, t in RUG_DOCS],
            },
        )
        assert dt < BUDGET_RERANK_10, f"{name} rerank took {dt}s"
        scores = {r["index"]: r["relevance_score"] for r in data["results"]}
        assert scores[0] > scores[1], (
            f"{name}: rug evidence ({scores[0]:.3f}) must outrank healthy pool ({scores[1]:.3f})"
        )
        assert scores[0] > 0.5, f"{name}: rug doc score too low ({scores[0]:.3f})"


@pytest.mark.asyncio
async def test_rerank_batch_of_ten_within_budget():
    docs = [t for _, t in RUG_DOCS] * 5
    async with aiohttp.ClientSession() as s:
        dt, data = await _post(
            s,
            f"{SPEED_RERANK}/rerank",
            {
                "query": RUG_QUERY_INSTRUCTED,
                "documents": docs,
            },
        )
        assert dt < BUDGET_RERANK_10
        assert len(data["results"]) == len(docs)


# ── chat engine ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_generation_clean_and_grounded():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=BUDGET_CHAT_80TOK)) as s:
        _dt, data = await _post(
            s,
            f"{CHAT}/v1/chat/completions",
            {
                "model": "qwen3-8b-instruct",
                "messages": [
                    {"role": "system", "content": "Answer strictly from context."},
                    {
                        "role": "user",
                        "content": "Context: TokenX has 85% deployer-owned LP and mint authority retained.\n"
                        "Question: In one sentence, what is TokenX's main rug-pull risk signal?",
                    },
                ],
                "max_tokens": 80,
            },
        )
        content = data["choices"][0]["message"]["content"]
        assert "<think>" not in content, "reasoning tags leaked into content"
        assert len(content.strip()) > 20, "empty/trivial answer"
        # grounding heuristic: the key fact must appear or be paraphrased closely
        assert any(kw in content.lower() for kw in ("85", "deployer", "lp", "liquidity")), (
            f"answer not grounded in context: {content[:120]!r}"
        )


@pytest.mark.asyncio
async def test_chat_parallel_two_slots():
    body = {
        "model": "qwen3-8b-instruct",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 10,
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=BUDGET_CHAT_80TOK)) as s:
        tasks = [_post(s, f"{CHAT}/v1/chat/completions", body) for _ in range(2)]
        results = await asyncio.gather(*tasks)
        assert all(d["choices"][0]["message"]["content"] for _, d in results)
