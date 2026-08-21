"""Monitor data routes: aggregated monitor status + per-monitor snapshots.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``. Lazy imports inside handlers (circular-safe), exactly as in
the original module.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])


@router.post("/rag/status/all")
async def rag_status_all(request: Request) -> dict[str, Any]:
    """Aggregated status data from ALL active monitors.

    Each monitor block is wrapped in try/except so a failing module
    never crashes the endpoint. Returns health + per-monitor state.
    """
    now = datetime.now(UTC)
    monitors: dict[str, Any] = {}
    health_checks: dict[str, Any] = {}

    # ── Hyperliquid ───────────────────────────────────────────────────
    try:
        from rugbrain.monitors import hyperliquid_monitor as _hl

        monitors["hyperliquid"] = {
            "coins_tracked": len(_hl._all_mids),
            "notable_trades": len(_hl._biggest_trades),
            "recent_liquidations": _hl._liquidation_history[-20:],
            "biggest_trades": sorted(
                _hl._biggest_trades,
                key=lambda t: t.get("usd_value", 0),
                reverse=True,
            )[:10],
        }
        health_checks["hyperliquid"] = {
            "status": "active" if len(_hl._all_mids) > 0 else "idle",
            "coins_tracked": len(_hl._all_mids),
            "trades_cached": len(_hl._biggest_trades),
            "liquidations_cached": len(_hl._liquidation_history),
        }
    except Exception as exc:
        monitors["hyperliquid"] = {"error": str(exc)[:200]}
        health_checks["hyperliquid"] = {"status": "error", "message": str(exc)[:200]}

    # ── Social Sentiment ──────────────────────────────────────────────
    try:
        from rugbrain.monitors.social_sentiment import (
            get_social_sentiment_state,
            social_sentiment_health,
        )

        monitors["social_sentiment"] = get_social_sentiment_state()
        health_checks["social_sentiment"] = await social_sentiment_health()
    except Exception as exc:
        monitors["social_sentiment"] = {"error": str(exc)[:200]}
        health_checks["social_sentiment"] = {"status": "error", "message": str(exc)[:200]}

    # ── Twitter Tracker ───────────────────────────────────────────────
    try:
        from rugbrain.monitors.twitter_tracker import (
            get_twitter_tracker_state,
            twitter_tracker_health,
        )

        monitors["twitter_tracker"] = get_twitter_tracker_state()
        health_checks["twitter_tracker"] = await twitter_tracker_health()
    except Exception as exc:
        monitors["twitter_tracker"] = {"error": str(exc)[:200]}
        health_checks["twitter_tracker"] = {"status": "error", "message": str(exc)[:200]}

    # ── DeFi Lending ──────────────────────────────────────────────────
    try:
        from rugbrain.monitors.defi_lending import (
            defi_lending_health,
            get_defi_lending_state,
        )

        monitors["defi_lending"] = get_defi_lending_state()
        health_checks["defi_lending"] = await defi_lending_health()
    except Exception as exc:
        monitors["defi_lending"] = {"error": str(exc)[:200]}
        health_checks["defi_lending"] = {"status": "error", "message": str(exc)[:200]}

    # ── Token Distribution ────────────────────────────────────────────
    try:
        from rugbrain.monitors.token_distribution import (
            get_airdrop_status,
            get_concentration_ranking,
            get_token_snapshot,
        )

        monitors["token_distribution"] = {
            "snapshot": get_token_snapshot(),
            "airdrop_status": get_airdrop_status(),
            "concentration_ranking": get_concentration_ranking()[:20],
        }
        health_checks["token_distribution"] = {
            "status": "active",
            "tokens_tracked": len(get_token_snapshot()),
        }
    except Exception as exc:
        monitors["token_distribution"] = {"error": str(exc)[:200]}
        health_checks["token_distribution"] = {"status": "error", "message": str(exc)[:200]}

    # ── Content Digest ────────────────────────────────────────────────
    try:
        from rugbrain.monitors.content_digest import get_digest

        monitors["content_digest"] = get_digest()
        health_checks["content_digest"] = {
            "status": "active" if get_digest() else "idle",
            "has_daily": bool(get_digest().get("daily")),
            "has_hourly": bool(get_digest().get("hourly")),
        }
    except Exception as exc:
        monitors["content_digest"] = {"error": str(exc)[:200]}
        health_checks["content_digest"] = {"status": "error", "message": str(exc)[:200]}

    # ── Liquidation Heatmap ───────────────────────────────────────────
    try:
        from rugbrain.liquidation_heatmap import (
            _7d_volume,
            _24h_volume,
            _30d_volume,
            _biggest_single,
            _liquidation_history,
            _peak_24h,
        )

        monitors["liquidation_heatmap"] = {
            "total_events": len(_liquidation_history),
            "volume_24h_usd": round(_24h_volume, 2),
            "volume_7d_usd": round(_7d_volume, 2),
            "volume_30d_usd": round(_30d_volume, 2),
            "peak_24h_usd": round(_peak_24h, 2),
            "biggest_single": _biggest_single,
            "recent_events": _liquidation_history[-20:],
        }
        health_checks["liquidation_heatmap"] = {
            "status": "active" if len(_liquidation_history) > 0 else "idle",
            "events_cached": len(_liquidation_history),
        }
    except Exception as exc:
        monitors["liquidation_heatmap"] = {"error": str(exc)[:200]}
        health_checks["liquidation_heatmap"] = {"status": "error", "message": str(exc)[:200]}

    # ── Exchange Flow ─────────────────────────────────────────────────
    try:
        from rugbrain.exchange_flow import exchange_flow_cycle

        monitors["exchange_flow"] = await exchange_flow_cycle()
        health_checks["exchange_flow"] = {
            "status": "active",
            "exchanges_scanned": len(monitors["exchange_flow"].get("exchanges", [])),
        }
    except Exception as exc:
        monitors["exchange_flow"] = {"error": str(exc)[:200]}
        health_checks["exchange_flow"] = {"status": "error", "message": str(exc)[:200]}

    # ── Market Health ─────────────────────────────────────────────────
    try:
        from rugbrain.market_health import market_health_cycle

        monitors["market_health"] = await market_health_cycle()
        health_checks["market_health"] = {"status": "active"}
    except Exception as exc:
        monitors["market_health"] = {"error": str(exc)[:200]}
        health_checks["market_health"] = {"status": "error", "message": str(exc)[:200]}

    return {
        "status": "ok",
        "monitors": monitors,
        "health": health_checks,
        "timestamp": now.isoformat(),
    }


@router.post("/rag/hyperliquid")
async def rag_hyperliquid(request: Request) -> dict[str, Any]:
    """Return Hyperliquid whale trades, liquidations, and funding snapshot."""
    try:
        from rugbrain.monitors.hyperliquid_monitor import hyperliquid_full_cycle

        data = await hyperliquid_full_cycle()
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.warning("rag_hyperliquid_failed: %s", exc)
        return {"status": "error", "message": str(exc)[:200]}


@router.post("/rag/social")
async def rag_social(request: Request) -> dict[str, Any]:
    """Aggregated social sentiment, Twitter tracker, and token distribution."""
    out: dict[str, Any] = {}
    errors: list[str] = []

    try:
        from rugbrain.monitors.social_sentiment import get_social_sentiment_state

        out["social_sentiment"] = get_social_sentiment_state()
    except Exception as exc:
        errors.append(f"social_sentiment: {exc}")
        out["social_sentiment"] = {"error": str(exc)[:200]}

    try:
        from rugbrain.monitors.twitter_tracker import get_twitter_tracker_state

        out["twitter_tracker"] = get_twitter_tracker_state()
    except Exception as exc:
        errors.append(f"twitter_tracker: {exc}")
        out["twitter_tracker"] = {"error": str(exc)[:200]}

    try:
        from rugbrain.monitors.token_distribution import (
            get_airdrop_status,
            get_concentration_ranking,
            get_token_snapshot,
        )

        out["token_distribution"] = {
            "snapshot": get_token_snapshot(),
            "airdrop_status": get_airdrop_status(),
            "concentration_ranking": get_concentration_ranking(),
        }
    except Exception as exc:
        errors.append(f"token_distribution: {exc}")
        out["token_distribution"] = {"error": str(exc)[:200]}

    return {
        "status": "ok" if not errors else "partial",
        "data": out,
        "errors": errors if errors else None,
    }


@router.post("/rag/defi")
async def rag_defi(request: Request) -> dict[str, Any]:
    """Aggregated DeFi lending pool health and liquidation data."""
    out: dict[str, Any] = {}
    errors: list[str] = []

    try:
        from rugbrain.monitors.defi_lending import get_defi_lending_state

        out["defi_lending"] = get_defi_lending_state()
    except Exception as exc:
        errors.append(f"defi_lending: {exc}")
        out["defi_lending"] = {"error": str(exc)[:200]}

    try:
        from rugbrain.liquidation_heatmap import liquidation_scan_cycle

        out["liquidation_heatmap"] = await liquidation_scan_cycle()
    except Exception as exc:
        errors.append(f"liquidation_heatmap: {exc}")
        out["liquidation_heatmap"] = {"error": str(exc)[:200]}

    return {
        "status": "ok" if not errors else "partial",
        "data": out,
        "errors": errors if errors else None,
    }


@router.post("/rag/digest")
async def rag_digest(request: Request) -> dict[str, Any]:
    """Return the latest content digest."""
    try:
        from rugbrain.monitors.content_digest import get_digest

        digest = get_digest()
        return {"status": "ok", "data": digest}
    except Exception as exc:
        logger.warning("rag_digest_failed: %s", exc)
        return {"status": "error", "message": str(exc)[:200]}
