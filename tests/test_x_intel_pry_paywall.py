"""Regression: a payment-gated (x402 402) Pry lane must fail the cycle loudly.

2026-09-26 incident: ``rmi-rag-x-ingest.service`` reported success every
10 minutes while scraping nothing — Pry answered 402, ``_pry_scrape`` swallowed
it at ``logger.debug`` and returned ``None``, ``run_cycle`` caught everything,
and the unit declared ``SuccessExitStatus=0 1`` so the failure was invisible.

These tests pin the three properties that close the hole:
1. a 402 raises ``PryLaneUnavailable`` (transient errors still fail-open);
2. the pipeline reports a dead lane and re-raises;
3. the CLI maps that to a nonzero exit code.

Zero ``monkeypatch``: every stub is injected through a production DI seam
(``scrape=``, ``pry_fetch=``, ``select=``, ``cycle=``) or passed as a
``MockTransport`` — the repo monkeypatch ratchet must not grow.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from rugbrain.ingest_sources.x_intel import __main__ as cli
from rugbrain.ingest_sources.x_intel import pipeline
from rugbrain.ingest_sources.x_intel.pry_lane import PryLaneUnavailable, scrape_tweet
from rugbrain.ingest_sources.x_intel.pry_paywall import PRY_LANE_EXIT_CODE, record_paywall

_TWEET_URL = "https://x.com/peckshield/status/999"


def _responder(status: int, **kwargs: Any) -> httpx.MockTransport:
    """A transport that answers every request with *status*."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, **kwargs)

    return httpx.MockTransport(handler)


def _dead_scrape(url: str, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    """``ScrapeFn`` stub standing in for a payment-gated Pry."""

    async def _inner() -> None:
        raise PryLaneUnavailable(f"402 for {url}")

    return _inner()


def _one_tweet(handles: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """``FetchPryFn`` stub: one healthy tweet, lane alive."""

    async def _inner() -> tuple[list[dict[str, Any]], bool]:
        assert handles == ["alice"]
        return [{"tweet_id": "1", "text": "exploit drained 5M", "handle": "alice"}], False

    return _inner()


def _no_accounts() -> tuple[list[str], list[str]]:
    """``SelectFn`` stub: nothing to fetch, so the cycle is a no-op."""

    async def _inner() -> tuple[list[str], list[str]]:
        return ([], [])

    return _inner()


# --- property 1: 402 escalates, everything else fails open ------------------


@pytest.mark.asyncio
async def test_402_raises_instead_of_swallowing() -> None:
    transport = _responder(402, json={"error": "x402 payment required"})
    with pytest.raises(PryLaneUnavailable, match="402"):
        await scrape_tweet(_TWEET_URL, transport=transport)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [_responder(503, text="unavailable")])
async def test_transient_5xx_still_fails_open(transport: httpx.MockTransport) -> None:
    """Only 402 escalates — a Pry hiccup must not fail the whole cycle."""
    assert await scrape_tweet("https://x.com/a/status/1", transport=transport) is None


@pytest.mark.asyncio
async def test_network_error_still_fails_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("pry down")

    assert (
        await scrape_tweet("https://x.com/a/status/1", transport=httpx.MockTransport(handler))
        is None
    )


@pytest.mark.asyncio
async def test_402_metric_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    """The 402 line is the hard metric — logged even with no Redis reachable.

    ``record_paywall`` is exercised directly (the ``transport``-based tests hit
    the live counter path, which is not what this property is about).
    """
    with caplog.at_level(logging.ERROR):
        await record_paywall()
    assert any(
        "pry_lane_402_total" in rec.getMessage() and "lane=dead" in rec.getMessage()
        for rec in caplog.records
    )


# --- property 2: the pipeline reports a dead lane and re-raises --------------


@pytest.mark.asyncio
async def test_fetch_pry_tweets_flags_dead_lane() -> None:
    tweets, dead = await pipeline._fetch_pry_tweets(["alice", "bob", "carol"], _dead_scrape)
    assert tweets == []
    assert dead is True


@pytest.mark.asyncio
async def test_fetch_pry_tweets_not_dead_on_transient_error() -> None:
    async def _down(handle: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        raise httpx.ConnectError("pry down")

    tweets, dead = await pipeline._fetch_pry_tweets(["alice", "bob"], _down)
    assert tweets == []
    assert dead is False


@pytest.mark.asyncio
async def test_run_cycle_raises_on_dead_pry_lane() -> None:
    def _dead(handles: list[str]) -> tuple[list[dict[str, Any]], bool]:
        assert handles == ["alice", "bob"]

        async def _inner() -> tuple[list[dict[str, Any]], bool]:
            return [], True

        return _inner()

    async def _select() -> tuple[list[str], list[str]]:
        return ([], ["alice", "bob"])

    with pytest.raises(PryLaneUnavailable, match="payment-gated"):
        await pipeline.run_cycle(select=_select, pry_fetch=_dead)


@pytest.mark.asyncio
async def test_run_cycle_succeeds_when_pry_returns_items() -> None:
    """Tweets flow through dedupe + Qdrant without a live store (fail-open)."""

    async def _select() -> tuple[list[str], list[str]]:
        return ([], ["alice"])

    summary = await pipeline.run_cycle(select=_select, pry_fetch=_one_tweet)
    assert summary["fetched"] == 1
    assert summary["getxapi_handles"] == 0
    assert summary["pry_handles"] == 1
    assert "pry_lane_dead" not in summary


@pytest.mark.asyncio
async def test_run_cycle_empty_accounts_is_a_clean_noop() -> None:
    summary = await pipeline.run_cycle(select=_no_accounts, pry_fetch=_one_tweet)
    assert summary["fetched"] == 0
    assert summary["unique"] == 0
    assert "error" not in summary


# --- property 3: the CLI surfaces it as a nonzero exit ----------------------


def test_cli_exit_code_on_dead_pry_lane() -> None:
    async def _boom() -> dict[str, Any]:
        raise PryLaneUnavailable("pry lane payment-gated (402) for all 2 handles")

    assert cli._run_cycle(_boom) == PRY_LANE_EXIT_CODE
    assert PRY_LANE_EXIT_CODE != 0


def test_cli_exit_code_zero_on_success() -> None:
    async def _ok() -> dict[str, Any]:
        return {"fetched": 3, "unique": 3, "ingested": 3}

    assert cli._run_cycle(_ok) == 0


def test_service_unit_does_not_mask_failure() -> None:
    """The unit must not whitelist a nonzero exit (that is what hid the bug)."""
    unit = (
        Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "rmi-rag-x-ingest.service"
    ).read_text(encoding="utf-8")
    success = [ln for ln in unit.splitlines() if ln.startswith("SuccessExitStatus")]
    assert success == ["SuccessExitStatus=0"], success
