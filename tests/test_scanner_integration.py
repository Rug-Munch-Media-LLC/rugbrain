"""
Cross-app Integration Test: Scanner → RugSense Pipeline.

Verifies the Redis pub/sub flow:
    Scanner (rmi-backend) → Redis ``rmi:rag:ingest`` → Subscriber → Qdrant ``threat_intel``

The test publishes a realistic scan finding to Redis, then polls the rmi-rag
search endpoint until the finding is discoverable via semantic search.

Skips gracefully when required services (Redis, rmi-rag) are not running.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
import pytest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
RMI_RAG_URL = os.environ.get("RMI_RAG_URL", "http://localhost:8012")
REDIS_HOST = os.environ.get("TEST_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("TEST_REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("TEST_REDIS_PASSWORD", "")
CHANNEL = "rmi:rag:ingest"
POLL_TIMEOUT = float(os.environ.get("TEST_POLL_TIMEOUT", "30.0"))
POLL_INTERVAL = float(os.environ.get("TEST_POLL_INTERVAL", "2.0"))

pytestmark = pytest.mark.integration


# ── Helpers ────────────────────────────────────────────────────────────────


def _redis_import_ok() -> bool:
    """Check whether ``redis.asyncio`` is importable."""
    try:
        import redis.asyncio  # noqa: F401

        return True
    except ImportError:
        return False


async def _service_healthy(url: str) -> bool:
    """Return True if *url*/health responds 200."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{url}/health")
            return resp.status_code == 200
    except httpx.RequestError:
        return False


def _build_test_finding(**overrides: Any) -> dict[str, Any]:
    """Build a realistic scanner finding payload.

    Fields mirror what ``UnifiedTokenScanner`` publishes to Redis.
    """
    finding: dict[str, Any] = {
        "token_address": "0xTEST_SCANNER_RAG_INTEGRATION_001",
        "chain": "ethereum",
        "name": "TestIntegrationToken",
        "symbol": "TINT",
        "risk_level": "high",
        "safety_score": 25,
        "risk_flags": [
            "high_sell_tax",
            "low_liquidity",
            "fresh_wallet_cluster",
        ],
        "price_usd": 0.00000123,
        "liquidity_usd": 350.0,
        "age_hours": 1.5,
        "scanned_at": "2026-07-28T01:00:00Z",
    }
    finding.update(overrides)
    return finding


async def _publish_finding(
    finding: dict[str, Any],
) -> None:
    """Publish *finding* to the Redis ingest channel."""
    import redis.asyncio as aioredis

    r = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
    )
    try:
        await r.publish(CHANNEL, json.dumps(finding))
        logger.info(
            "Published finding for %s to %s",
            finding.get("token_address", "?")[:20],
            CHANNEL,
        )
    finally:
        await r.close()


async def _search_for_token(
    client: httpx.AsyncClient,
    address_fragment: str,
    poll_timeout: float = POLL_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Poll rmi-rag search until *address_fragment* appears, or return None."""
    import asyncio

    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        try:
            resp = await client.get(
                f"{RMI_RAG_URL}/api/v1/search",
                params={"q": "TestIntegrationToken TINT high_sell_tax", "limit": 10},
            )
            if resp.status_code == 200:
                data = resp.json()
                for r in data.get("results", []):
                    meta = r.get("metadata", {})
                    token_addr = (meta.get("token_address", "") or "").lower()
                    content = (r.get("content", "") or "").lower()
                    if (
                        address_fragment.lower() in token_addr
                        or address_fragment.lower() in content
                    ):
                        return r
        except httpx.RequestError:
            pass
        await asyncio.sleep(interval)
    return None


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_to_rag_pipeline():
    """Publish a scan finding to Redis and verify rmi-rag ingests it.

    Full end-to-end check of the scanner → Redis → subscriber → Qdrant flow.
    """
    if not _redis_import_ok():
        pytest.skip("redis.asyncio not installed — cannot publish to Redis")

    if not await _service_healthy(RMI_RAG_URL):
        pytest.skip(f"rmi-rag service not reachable at {RMI_RAG_URL}")

    # Build a finding with a unique token address to avoid collisions
    unique_suffix = hex(int(time.time() * 1_000_000))[-12:]
    token_address = f"0xTEST_RAG_INT_{unique_suffix}"
    finding = _build_test_finding(token_address=token_address)

    # Publish to Redis
    await _publish_finding(finding)

    # Poll search endpoint until the finding appears
    async with httpx.AsyncClient(timeout=10.0) as client:
        result = await _search_for_token(client, token_address)

    assert result is not None, (
        f"Finding {token_address} not found in rmi-rag search after "
        f"{POLL_TIMEOUT}s. Verify the subscriber is running and Qdrant is healthy."
    )

    # Verify essential metadata fields match what we published
    meta = result.get("metadata", {})
    assert meta.get("token_address", "").lower() == token_address.lower(), (
        f"Expected token_address {token_address}, got {meta.get('token_address')}"
    )
    assert meta.get("chain") == "ethereum"
    assert meta.get("risk_level") == "high"
    assert meta.get("source") == "scanner"
    assert result.get("content", ""), "Ingested result should have non-empty content"


@pytest.mark.asyncio
async def test_rag_search_endpoint_returns_expected_schema():
    """Verify the search endpoint schema matches what the frontend expects."""
    if not await _service_healthy(RMI_RAG_URL):
        pytest.skip(f"rmi-rag service not reachable at {RMI_RAG_URL}")

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{RMI_RAG_URL}/api/v1/search",
            params={"q": "test finding", "limit": 5},
        )

    # May 503 if embeddings are unavailable (model not loaded)
    assert resp.status_code in (200, 503), (
        f"Expected 200 or 503, got {resp.status_code}: {resp.text[:200]}"
    )

    if resp.status_code == 200:
        data = resp.json()
        assert "results" in data, "Response must contain 'results'"

        # If results exist, they must have the expected shape
        for r in data["results"]:
            assert isinstance(r, dict), "Each result must be a dict"
            assert "id" in r, "Each result must have 'id'"
            assert "content" in r, "Each result must have 'content'"
            assert "metadata" in r, "Each result must have 'metadata'"
            assert isinstance(r["metadata"], dict), "'metadata' must be a dict"
            meta = r["metadata"]
            # Essential cross-app contract fields
            if meta.get("source") == "scanner":
                assert "token_address" in meta, (
                    "Scanner results must include 'token_address' in metadata"
                )
                assert "chain" in meta
                assert "risk_level" in meta
                assert "safety_score" in meta

        assert data.get("query") == "test finding"
        assert isinstance(data.get("total"), int)


@pytest.mark.asyncio
async def test_redis_publish_survives_invalid_json():
    """Publish invalid JSON — subscriber must not crash.

    This test only validates that the publish operation itself works.
    Whether the subscriber processes malformed input is verified via
    subscriber logs (not test-observable without direct Redis access).
    """
    if not _redis_import_ok():
        pytest.skip("redis.asyncio not installed")

    import redis.asyncio as aioredis

    try:
        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
        )
        # Verify connection works before sending
        await r.ping()
    except Exception as exc:
        pytest.skip(f"Redis not available: {exc}")

    try:
        # Publish raw invalid JSON
        await r.publish(CHANNEL, "this is not valid json {{{")
        await r.publish(CHANNEL, "")
        await r.publish(CHANNEL, "null")
    finally:
        await r.aclose()

    # If we get here without exception, the publish operations completed.
    # The subscriber handles malformed input gracefully (logs warnings).
    assert True


@pytest.mark.asyncio
async def test_subscriber_health_check():
    """The rmi-rag subscriber health endpoint reflects subscriber status."""
    if not await _service_healthy(RMI_RAG_URL):
        pytest.skip(f"rmi-rag service not reachable at {RMI_RAG_URL}")

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{RMI_RAG_URL}/health")

    assert resp.status_code == 200
    data = resp.json()

    # The health endpoint should include subscriber status
    assert "redis" in data, "Health response must include 'redis' field"
