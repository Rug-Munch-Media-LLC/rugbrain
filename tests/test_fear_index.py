"""Tests for the crypto panic/fear index module."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

import rugbrain.fear_index as fi

# ═══════════════════════════════════════════════════════════════════════
# Classification + composite
# ═══════════════════════════════════════════════════════════════════════


def test_classify_fear_boundaries() -> None:
    assert fi.classify_fear(0) == "extreme_fear"
    assert fi.classify_fear(24) == "extreme_fear"
    assert fi.classify_fear(25) == "fear"
    assert fi.classify_fear(39) == "fear"
    assert fi.classify_fear(40) == "neutral"
    assert fi.classify_fear(60) == "neutral"
    assert fi.classify_fear(61) == "greed"
    assert fi.classify_fear(74) == "greed"
    assert fi.classify_fear(75) == "extreme_greed"
    assert fi.classify_fear(100) == "extreme_greed"


def test_compute_fear_index_weighted_all_components() -> None:
    components = {"fng": 80, "social": 60, "onchain": 40, "news": 50}
    expected = round((0.35 * 80 + 0.20 * 60 + 0.25 * 40 + 0.20 * 50) / (0.35 + 0.20 + 0.25 + 0.20))
    assert fi.compute_fear_index(components) == expected


def test_compute_fear_index_renormalizes_missing() -> None:
    assert fi.compute_fear_index({"fng": 80}) == 80
    assert fi.compute_fear_index({"fng": None, "news": 20}) == 20


def test_compute_fear_index_empty_neutral() -> None:
    assert fi.compute_fear_index({}) == 50
    assert fi.compute_fear_index({"fng": None, "social": None}) == 50


def test_aggregate_news_sentiment() -> None:
    assert fi.aggregate_news_sentiment([1.0, -1.0, 0.0]) == 50
    assert fi.aggregate_news_sentiment([1.0, 1.0]) == 100
    assert fi.aggregate_news_sentiment([]) is None


def test_fear_snapshot_shape() -> None:
    snapshot = fi.fear_snapshot({"fng": 30, "social": None})
    assert snapshot["value"] == 30
    assert snapshot["classification"] == "fear"
    assert snapshot["components"]["social"] is None
    assert snapshot["timestamp"]


# ═══════════════════════════════════════════════════════════════════════
# Component fetchers
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fng_returns_value() -> None:
    respx.get(fi.FNG_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [{"value": "45", "value_classification": "Fear"}]}
        )
    )
    async with httpx.AsyncClient() as client:
        assert await fi.fetch_fng(client) == 45


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fng_failure_returns_none() -> None:
    respx.get(fi.FNG_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        assert await fi.fetch_fng(client) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_social_sentiment_positive() -> None:
    respx.get(fi.SEARXNG_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Bitcoin surges to new all-time high",
                        "content": "bull market rally",
                    },
                    {"title": "Ethereum recovery continues", "content": "optimism returns"},
                    {"title": "Crypto market dips on fear", "content": "panic selloff"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        value = await fi.fetch_social_sentiment(client)
        assert value is not None
        assert value > 50


@pytest.mark.asyncio
@respx.mock
async def test_fetch_social_sentiment_failure_returns_none() -> None:
    respx.get(fi.SEARXNG_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        assert await fi.fetch_social_sentiment(client) is None


@pytest.mark.asyncio
async def test_fetch_onchain_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fi, "ONCHAIN_SIGNALS_URL", "")
    async with httpx.AsyncClient() as client:
        assert await fi.fetch_onchain_signals(client) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_onchain_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fi, "ONCHAIN_SIGNALS_URL", "http://onchain.test/feed")
    # Positive netflow = inflows = fear → invert toward 0 (fear).
    respx.get("http://onchain.test/feed").mock(
        return_value=httpx.Response(
            200,
            json={"exchange_netflow": 60, "whale_movements": -20, "liquidations": 40},
        )
    )
    async with httpx.AsyncClient() as client:
        value = await fi.fetch_onchain_signals(client)
        assert value is not None
        assert 0 <= value <= 100


@pytest.mark.asyncio
async def test_fetch_news_sentiment_aggregates(monkeypatch: pytest.MonkeyPatch) -> Any:
    """fetch_news_sentiment uses Qdrant scroll; monkeypatch to a fake store."""

    class FakeStore:
        async def scroll(self, collection: Any, limit: Any) -> Any:
            return (
                [
                    {"metadata": {"sentiment": 0.8}},
                    {"metadata": {"sentiment": -0.2}},
                    {"metadata": {"sentiment": "neutral"}},
                    {"metadata": {}},
                ],
                None,
            )

    import rugbrain.qdrant_store as qs

    monkeypatch.setattr(qs, "QdrantVectorStore", FakeStore)
    # (0.8 + -0.2 + 0.0) / 3 = 0.2 → (0.2 + 1) / 2 * 100 = 60
    assert await fi.fetch_news_sentiment() == 60


# ═══════════════════════════════════════════════════════════════════════
# Risk scoring feed
# ═══════════════════════════════════════════════════════════════════════


def test_adjust_risk_extreme_fear_escalates() -> None:
    verdict = {"risk": "low", "confidence": 0.5, "top_pattern": "x"}
    out = fi.adjust_risk_for_market(verdict, 20)
    assert out["risk"] == "medium"
    assert out["fear_adjusted"] is True
    assert "fear_market_risk_boost" in out["risk_signals"]
    assert out["fear_index"] == 20


def test_adjust_risk_fear_escalates_only_low() -> None:
    assert fi.adjust_risk_for_market({"risk": "low"}, 30)["risk"] == "medium"
    assert fi.adjust_risk_for_market({"risk": "medium"}, 30)["risk"] == "medium"


def test_adjust_risk_neutral_no_change() -> None:
    out = fi.adjust_risk_for_market({"risk": "low"}, 55)
    assert out["risk"] == "low"
    assert out["fear_adjusted"] is False


def test_adjust_risk_none_no_change() -> None:
    out = fi.adjust_risk_for_market({"risk": "high"}, None)
    assert out["risk"] == "high"
    assert out["fear_index"] is None
    assert out["fear_adjusted"] is False


# ═══════════════════════════════════════════════════════════════════════
# Polling + API helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@respx.mock
async def test_poll_fear_index_full(monkeypatch: pytest.MonkeyPatch) -> Any:
    respx.get(fi.FNG_URL).mock(return_value=httpx.Response(200, json={"data": [{"value": "45"}]}))
    respx.get(fi.SEARXNG_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "Bitcoin surges", "content": "bull rally"}]}
        )
    )
    monkeypatch.setattr(fi, "ONCHAIN_SIGNALS_URL", "")

    async def fake_news(_client: Any = None) -> Any:
        return 55

    monkeypatch.setattr(fi, "fetch_news_sentiment", fake_news)
    monkeypatch.setattr(fi, "_cache_set", _noop_async)
    monkeypatch.setattr(fi, "_storage", _FakeStorage())

    snapshot = await fi.poll_fear_index(use_cache=False)
    # fng=45 (0.35) + social=100 (0.20) + news=55 (0.20); onchain omitted.
    # weighted = 15.75 + 20 + 11 = 46.75 → / 0.75 = 62.33 → 62 (greed band)
    assert snapshot["value"] == 62
    assert snapshot["classification"] == "greed"
    assert snapshot["components"]["onchain"] is None


@pytest.mark.asyncio
async def test_get_fear_index_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fi, "_cache_get", _noop_async_none)
    snapshot = fi.fear_snapshot({"fng": 30})
    fi._record(snapshot)
    try:
        result = await fi.get_fear_index(window="24h")
        assert result["value"] == 30
        assert result["source"] == "memory"
    finally:
        fi._history.clear()
        fi._last_snapshot = None


@pytest.mark.asyncio
async def test_fear_index_history_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fi._storage, "query_history", lambda days: [])
    snapshot = fi.fear_snapshot({"fng": 80})
    fi._record(snapshot)
    try:
        result = await fi.fear_index_history(days=30)
        assert result["source"] == "memory"
        assert len(result["points"]) >= 1
        assert "value" in result["points"][0]
    finally:
        fi._history.clear()
        fi._last_snapshot = None


@pytest.mark.asyncio
async def test_fear_index_history_empty() -> None:
    fi._history.clear()
    fi._last_snapshot = None
    result = await fi.fear_index_history(days=30)
    assert result["source"] == "empty"
    assert result["points"] == []


@pytest.mark.asyncio
async def test_poller_start_stop() -> None:
    await fi.stop_fear_index_poller()
    assert fi.is_fear_index_poller_running() is False
    await fi.start_fear_index_poller()
    try:
        assert fi.is_fear_index_poller_running() is True
        # Idempotent
        await fi.start_fear_index_poller()
        assert fi.is_fear_index_poller_running() is True
    finally:
        await fi.stop_fear_index_poller()
    assert fi.is_fear_index_poller_running() is False


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    return None


async def _noop_async_none(*args: Any, **kwargs: Any) -> None:
    return None


class _FakeStorage:
    def insert(self, snapshot: Any) -> Any:
        return True

    def query_history(self, days: Any) -> Any:
        return []


def test_window_samples_respects_window() -> None:
    fi._history.clear()
    now = datetime.now(UTC)
    fi._record(
        {"timestamp": (now - timedelta(hours=2)).isoformat(), "value": 40, "classification": "fear"}
    )
    fi._record({"timestamp": now.isoformat(), "value": 60, "classification": "neutral"})
    try:
        # 1h window keeps only the recent sample.
        samples = fi._history
        assert len(samples) == 2
    finally:
        fi._history.clear()
