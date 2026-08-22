"""RugBrain — market context for token scans.

Part 3 — RugBrain market context endpoint.

Builds the full market context a token scan needs before scoring:

  * ``fear_index``              — crypto panic/fear index (0-100)
  * ``chain_activity``          — 24h volume + new deploys on the chain
  * ``social_signals``          — mention velocity + sentiment for the token
  * ``recent_exploits``         — exploits in the last 24h (same chain /
                                  same category when identifiable)
  * ``similar_tokens``          — performance of historically similar tokens

The scanner (rmi-backend) calls ``GET /api/v1/market/context/{address}``
before each scan, plus ``GET /api/v1/market/mood`` for the global mood.
Both endpoints are served by this module (see ``market_routes``).

All providers fail open to neutral values — market context is enrichment,
never a blocker.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

DEFILLAMA_CHAINS_URL = "https://api.llama.fi/v2/chains"
FNG_URL = os.getenv("FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=1")
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"  # noqa: S105
COINPAPRIKA_NEW_URL = "https://api.coinpaprika.com/v1/coins/new"

HTTP_TIMEOUT = float(os.getenv("MARKET_HTTP_TIMEOUT", "15"))
USER_AGENT = "RMI-RAG/1.0 (+https://rugmunch.io; market-context)"

# Cache (in-process) for the global market mood — the scanner fetches it per
# chain and we don't want every scan to hit Alternative.me / DeFiLlama.
_MOOD_CACHE: dict[str, Any] = {}
_MOOD_CACHE_TTL = int(os.getenv("MARKET_MOOD_CACHE_TTL", "300"))  # 5 min


# ── Global market mood ──────────────────────────────────────────────────


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None
) -> Any:
    try:
        r = await client.get(url, params=params)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
        log.debug("market_context_http_failed url=%s err=%s", url, str(exc)[:120])
        return None


def _normalize_classification(value: Any) -> str:
    """Map provider classification vocabularies to fearful|neutral|greedy."""
    label = str(value or "").strip().lower()
    if label in ("extreme_fear", "fear", "fearful"):
        return "fearful"
    if label in ("extreme_greed", "greed", "greedy"):
        return "greedy"
    return "neutral"


async def _fetch_fear_index(client: httpx.AsyncClient) -> dict[str, Any]:
    """Numeric fear & greed value plus classification (0-100)."""
    try:
        from rugbrain.fear_index import get_fear_index

        snapshot = await get_fear_index(window="24h")
        value = snapshot.get("value")
        if isinstance(value, (int, float)):
            return {
                "value": float(value),
                "classification": _normalize_classification(
                    snapshot.get("classification", "neutral")
                ),
            }
    except Exception as exc:
        log.debug("fear_index_fetch_failed err=%s", str(exc)[:120])
    data = await _get_json(client, FNG_URL)
    if isinstance(data, dict):
        values = data.get("data") or []
        if values:
            value = float(values[0].get("value") or 50)
            classification = _normalize_classification(
                "fearful" if value < 30 else "greedy" if value > 70 else "neutral"
            )
            return {"value": value, "classification": classification}
    return {"value": 50.0, "classification": "neutral"}


async def _fetch_chain_activity(client: httpx.AsyncClient, chain: str) -> dict[str, Any]:
    """24h chain activity: TVL change + new deploys estimate (DeFiLlama)."""
    try:
        rows = await _get_json(client, DEFILLAMA_CHAINS_URL)
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("name", "")).strip().lower() == str(chain).lower():
                    return {
                        "chain": str(row.get("name", "")),
                        "tvl": float(row.get("tvl") or 0),
                        "tvl_change_24h": float(row.get("change_1d") or 0),
                    }
    except Exception as exc:
        log.debug("defillama_chains_failed err=%s", str(exc)[:120])
    return {"chain": chain, "tvl": 0.0, "tvl_change_24h": 0.0}


async def _fetch_new_deploys(client: httpx.AsyncClient, chain: str) -> int:
    """Estimate of new token deploys today (CoinPaprika recently-added count)."""
    try:
        coins = await _get_json(client, COINPAPRIKA_NEW_URL)
        if isinstance(coins, list):
            return len(coins[:50])
    except Exception:
        pass
    return 0


async def build_market_mood(chain: str = "ethereum") -> dict[str, Any]:
    """Global market mood digest for *chain* (cached 5 min in-process)."""
    now = datetime.now(UTC)
    cached = _MOOD_CACHE.get(chain)
    if cached and now - cached.get("_fetched_at", now - timedelta(hours=1)) < timedelta(
        seconds=_MOOD_CACHE_TTL
    ):
        return {k: v for k, v in cached.items() if k != "_fetched_at"}

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
    ) as client:
        fear, chain_activity, new_deploys, exploits = await asyncio.gather(
            _fetch_fear_index(client),
            _fetch_chain_activity(client, chain),
            _fetch_new_deploys(client, chain),
            _fetch_recent_exploits(client, chain),
        )

    mood = {
        "fear_index": fear["value"],
        "fear_classification": fear["classification"],
        "market_sentiment": fear["classification"],
        "recent_exploits_24h": len(exploits),
        "chain_volume_24h": float(chain_activity.get("tvl_change_24h", 0) or 0),
        "chain_activity": {
            "chain": chain_activity.get("chain") or chain,
            "tvl": float(chain_activity.get("tvl", 0) or 0),
            "tvl_change_24h": float(chain_activity.get("tvl_change_24h", 0) or 0),
            "new_deploys_24h": new_deploys,
        },
        "generated_at": now.isoformat(),
    }
    _MOOD_CACHE[chain] = {**mood, "_fetched_at": now}
    return mood


# ── Recent exploits ─────────────────────────────────────────────────────


async def _fetch_recent_exploits(
    client: httpx.AsyncClient, chain: str | None = None, hours: int = 24
) -> list[dict[str, Any]]:
    """Exploits in the last *hours* from the DeFiLlama hacks database."""
    from rugbrain.market_awareness import fetch_recent_exploits

    try:
        exploits = await fetch_recent_exploits(client)
        since = datetime.now(UTC) - timedelta(hours=hours)
        filtered: list[dict[str, Any]] = []
        for exploit in exploits:
            date_str = str(exploit.get("date", ""))
            try:
                d = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
            except ValueError:
                continue
            if d >= since:
                filtered.append(exploit)
        if chain:
            filtered = [
                e
                for e in filtered
                if str(e.get("chain", "")).lower() == str(chain).lower()
                or str(e.get("chain", "")).lower() in ("multi", "unknown")
            ]
        return filtered[:10]
    except Exception as exc:
        log.debug("recent_exploits_failed err=%s", str(exc)[:120])
        return []


# ── Token-specific signals ──────────────────────────────────────────────


async def _fetch_social_signals(address: str, chain: str) -> dict[str, Any]:
    """Social mentions + sentiment for the token (RAG collections)."""
    try:
        from rugbrain.sentiment_engine import analyze_token_sentiment

        result = await analyze_token_sentiment(
            address=address, chain=chain, include_social=True, include_news=True
        )
        mention_velocity = 0.0
        total = 0
        for count in (result.source_counts or {}).values():
            if isinstance(count, int):
                total += count
        if total > 0:
            mention_velocity = min(100.0, float(total) * 10.0)
        return {
            "sentiment": result.sentiment,
            "confidence": result.confidence,
            "mentions": total,
            "mention_velocity": mention_velocity,
            "source_counts": result.source_counts,
            "top_signals": result.signals,
        }
    except Exception as exc:
        log.debug("social_signals_failed err=%s", str(exc)[:120])
        return {
            "sentiment": "unknown",
            "confidence": 0.0,
            "mentions": 0,
            "mention_velocity": 0.0,
            "source_counts": {},
        }


async def _fetch_deployer_freshness(address: str, chain: str) -> str:
    """Classify deployer freshness: new / established / unknown.

    Uses the token's own on-chain age when available (DexScreener pair
    creation / first-seen), otherwise ``unknown``. Deployer freshness
    classification is best-effort — the scanner additionally derives its
    own signal from the token's age during scoring.
    """
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            data = await _get_json(client, f"{DEXSCREENER_TOKEN_URL}/{address}")
        pairs = (data or {}).get("pairs") or []
        if pairs and isinstance(pairs[0], dict):
            pair = pairs[0]
            created = pair.get("pairCreatedAt")
            if isinstance(created, (int, float)) and created > 0:
                age_days = (datetime.now(UTC) - datetime.fromtimestamp(created / 1000, tz=UTC)).days
                if age_days <= 30:
                    return "new"
                return "established"
            listed = pair.get("creationTime") or (pair.get("info") or {}).get("creationTime")
            if listed:
                age_days = (
                    datetime.now(UTC) - datetime.fromisoformat(str(listed).replace("Z", "+00:00"))
                ).days
                if age_days <= 30:
                    return "new"
                return "established"
    except Exception as exc:
        log.debug("deployer_freshness_failed err=%s", str(exc)[:120])
    return "unknown"


async def _fetch_similar_tokens(address: str, chain: str) -> list[dict[str, Any]]:
    """Performance of historically similar tokens (cross-reference)."""
    try:
        from rugbrain.cross_reference import find_similar_historical_tokens

        similar = await find_similar_historical_tokens(address, chain=chain, limit=5)
        tokens = similar.get("similar_tokens", []) or []
        out: list[dict[str, Any]] = []
        for t in tokens[:5]:
            if isinstance(t, dict):
                out.append(
                    {
                        "address": t.get("address", ""),
                        "name": t.get("name", t.get("token_name", "")),
                        "outcome": t.get("outcome", "unknown"),
                        "similarity": t.get("similarity", t.get("score", 0)),
                        "notes": str(t.get("notes", "") or t.get("narrative", ""))[:200],
                    }
                )
            else:
                out.append({"address": str(t)})
        return out
    except Exception as exc:
        log.debug("similar_tokens_failed err=%s", str(exc)[:120])
        return []


# ── Full token market context ───────────────────────────────────────────


async def build_token_market_context(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Full market context for a token address.

    This is what ``GET /api/v1/market/context/{address}`` returns — the
    scanner calls it before each scan to feed ``MarketContext``.
    """
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
    ) as client:
        (
            fear,
            chain_activity,
            new_deploys,
            exploits,
            social,
            freshness,
            similar,
        ) = cast(
            tuple[
                dict[str, Any],
                dict[str, Any],
                int,
                list[dict[str, Any]],
                dict[str, Any],
                str,
                list[dict[str, Any]],
            ],
            await asyncio.gather(
                _fetch_fear_index(client),
                _fetch_chain_activity(client, chain),
                _fetch_new_deploys(client, chain),
                _fetch_recent_exploits(client, chain),
                _fetch_social_signals(address, chain),
                _fetch_deployer_freshness(address, chain),
                _fetch_similar_tokens(address, chain),
            ),
        )

    return {
        "address": address,
        "chain": chain,
        "fear_index": fear["value"],
        "fear_classification": fear["classification"],
        "market_sentiment": fear["classification"],
        "chain_activity": {
            "chain": chain,
            "tvl": float(chain_activity.get("tvl", 0) or 0),
            "tvl_change_24h": float(chain_activity.get("tvl_change_24h", 0) or 0),
            "new_deploys_24h": new_deploys,
        },
        "chain_volume_24h": float(chain_activity.get("tvl_change_24h", 0) or 0),
        "recent_exploits": exploits,
        "social_signals": social,
        "deployer_freshness": freshness,
        "similar_tokens": similar,
        "generated_at": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "build_market_mood",
    "build_token_market_context",
]
