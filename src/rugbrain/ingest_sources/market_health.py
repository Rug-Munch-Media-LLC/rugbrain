"""Market-health source — the ingest-path home for the orphaned market fetchers.

WHY THIS MODULE EXISTS
    ``rugbrain/firehose/market_sources.py`` carried four working fetchers
    (``fetch_market_health``, ``fetch_stablecoin_market``,
    ``fetch_derivatives_pressure``, ``fetch_trending_snapshot``) with **zero
    importers**. The firehose engine never registered them, so
    ``market_health_8b`` stopped receiving writes on 2026-09-11 and sat at 627
    points while the taxonomy promised live market awareness.

    The real ingest path is ``scripts/ingest_all_sources.py`` over
    ``rugbrain.ingest_sources.SOURCES``. This module adapts the firehose
    fetchers into that contract (``Source._fetch -> list[IngestDoc]``) so the
    market collections are fed by the pipeline that actually runs, not by a
    module nothing imports.

One job: fetch the free market feeds and normalize them into ``IngestDoc``.
Chunking, embedding and Qdrant upserts stay in the ingest pipeline.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

#: The canonical lane name. ``lane_table`` declares ``market_health`` a twin
#: base, so the store rewrites this to ``market_health_8b`` (4096) on write.
DEFAULT_COLLECTION = "market_health"
DOC_TYPE = "market_data"

_UA = {"User-Agent": "rugbrain-ingest/1.0 (+https://rugmunch.io)"}


def _now_iso() -> str:
    """ISO stamp so downstream freshness gates can rank intel docs."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _doc_id(source: str, content: str) -> str:
    """Deterministic id: ``<source>:<content hash>``.

    Content-hashed so a re-run of the same snapshot is idempotent (the store's
    ``has_point`` skips it) while a changed snapshot lands as a new point.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def _doc(content: str, meta: dict[str, Any]) -> IngestDoc:
    """Build one IngestDoc with the firehose metadata convention preserved."""
    source = str(meta.get("source", "market"))
    metadata = {"published": _now_iso(), "collection": DEFAULT_COLLECTION, **meta}
    return IngestDoc(
        content=content[:2000],
        doc_id=_doc_id(source, content),
        metadata=metadata,
        doc_type=DOC_TYPE,
        source=source,
    )


async def _global_metrics(client: httpx.AsyncClient) -> list[IngestDoc]:
    """CoinGecko global metrics → market_health."""
    try:
        r = await client.get("https://api.coingecko.com/api/v3/global")
        if r.status_code != 200:
            return []
        d = r.json().get("data", {})
        mcap_t = d.get("total_market_cap", {}).get("usd", 0)
        mcap = f"{mcap_t / 1e12:.2f}T" if mcap_t > 1e12 else f"{mcap_t / 1e9:.0f}B"
        return [
            _doc(
                f"Global crypto market cap ${mcap}, BTC dominance "
                f"{d.get('market_cap_percentage', {}).get('btc', 0):.1f}%, "
                f"24h volume ${d.get('total_volume', {}).get('usd', 0) / 1e9:.1f}B, "
                f"active coins {d.get('active_cryptocurrencies', '?')}",
                {"source": "coingecko_global", "type": "daily_summary"},
            )
        ]
    except Exception as exc:
        log.debug("global metrics failed: %s", exc)
        return []


async def _fear_greed(client: httpx.AsyncClient) -> list[IngestDoc]:
    """alternative.me Fear & Greed → market_health (sentiment component)."""
    try:
        r = await client.get("https://api.alternative.me/fng/", params={"limit": 1})
        if r.status_code != 200:
            return []
        rows = r.json().get("data") or []
        if not rows:
            return []
        return [
            _doc(
                f"Fear & Greed Index: {rows[0].get('value')} "
                f"({rows[0].get('value_classification')})",
                {"source": "fear_greed", "type": "sentiment"},
            )
        ]
    except Exception as exc:
        log.debug("fng failed: %s", exc)
        return []


async def _derivatives(client: httpx.AsyncClient) -> list[IngestDoc]:
    """CoinGecko derivatives open interest → market_health.

    Binance fapi is 451-geo-blocked from this host and Bybit 403s; CoinGecko's
    free derivatives endpoint carries the same signal.
    """
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/derivatives/exchanges",
            params={"per_page": 5},
        )
        if r.status_code != 200:
            return []
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return []
        parts = [
            f"{x.get('name', '?')} {float(x.get('open_interest_btc') or 0):,.0f} BTC"
            for x in rows[:5]
        ]
        return [
            _doc(
                f"Derivatives open interest leaders: {', '.join(parts)}",
                {"source": "coingecko_derivatives", "type": "snapshot"},
            )
        ]
    except Exception as exc:
        log.debug("derivatives pull failed: %s", exc)
        return []


class MarketHealthSource(Source):
    """CoinGecko global + Fear&Greed + derivatives → ``market_health``.

    Registered in ``registry.SOURCES`` so ``scripts/ingest_all_sources.py``
    feeds the collection on its normal cadence. Fail-open per feed: one dead
    upstream never empties the whole source.
    """

    name = "market_health"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "CoinGecko global/FNG/derivatives market-health snapshot"

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        docs: list[IngestDoc] = []
        for fetch in (_global_metrics, _fear_greed, _derivatives):
            docs.extend(await fetch(client))
        return docs[:limit] if limit else docs


__all__ = ["DEFAULT_COLLECTION", "DOC_TYPE", "MarketHealthSource"]
