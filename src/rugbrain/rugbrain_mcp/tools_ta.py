"""TA + prediction-market tools — the MCP surface over /api/v1/ta/* and the
prediction_markets corpus lane.

The TA substrate (:mod:`rugbrain.ta`) and its routes shipped with no MCP tool,
so an agent could not ask "RSI for this pair" or "what are the odds". Every
failure below is a TYPED REFUSAL (``status: refused`` + ``error.code``) — an
unavailable store is never reported as an empty success.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult
from pydantic import Field

from rugbrain.rugbrain_mcp.app import _call, mcp
from rugbrain.rugbrain_mcp.market_helpers import _call_search_multi

HOURS = Field(default=168, ge=24, le=8760, description="Lookback window in hours (24..8760).")
CHAIN = Field(default="solana", description="Chain id, e.g. 'solana' or 'ethereum'.")


def _ok(tool: str, payload: dict[str, Any], text: str) -> ToolResult:
    """Success envelope — same ToolResult shape as the other rugbrain tools."""
    return ToolResult(
        content=[{"type": "text", "text": text}],
        structured_content={"status": "ok", "tool": tool, **payload},
    )


def _refuse(tool: str, code: str, reason: str, **extra: Any) -> ToolResult:
    """Typed refusal — the caller can branch on ``error.code``, never on emptiness."""
    payload = {"status": "refused", "tool": tool, "error": {"code": code, "reason": reason}, **extra}
    return ToolResult(
        content=[{"type": "text", "text": f"refused[{code}]: {reason}"}],
        structured_content=payload,
    )


def _classify(tool: str, exc: Exception, pair: str, chain: str) -> ToolResult:
    """Map an upstream HTTP failure onto a typed refusal (503 is data, not a bug)."""
    msg = str(exc)
    if "HTTP 503" in msg:
        return _refuse(tool, "ohlcv_unavailable", "OHLCV store unavailable; no candles for this pair", pair=pair, chain=chain)
    if "HTTP 400" in msg:
        return _refuse(tool, "invalid_argument", msg[:300], pair=pair, chain=chain)
    if "HTTP 401" in msg or "HTTP 403" in msg:
        return _refuse(tool, "auth_failed", msg[:200], pair=pair, chain=chain)
    return _refuse(tool, "upstream_error", msg[:300], pair=pair, chain=chain)


def _window_ok(hours: int) -> bool:
    """The route validates too; this keeps a direct call from scanning the table."""
    return 24 <= hours <= 8760


@mcp.tool()
async def ta_signals(
    pair: str = Field(description="DEX pair / pool address."),
    hours: int = HOURS,
    chain: str = CHAIN,
) -> ToolResult:
    """Evaluated TA signals for one DEX pair: liquidity collapse, abnormal volume,
    dump pattern, plus the trend state at the latest hourly candle."""
    if not pair.strip():
        return _refuse("ta_signals", "invalid_argument", "pair must be a non-empty DEX pair address")
    if not _window_ok(hours):
        return _refuse("ta_signals", "invalid_argument", "hours must be within 24..8760")
    try:
        data = _call("/api/v1/ta/signals", {"chain": chain, "pair": pair.strip(), "hours": hours})
    except RuntimeError as exc:
        return _classify("ta_signals", exc, pair, chain)
    sigs = data.get("signals") or []
    return _ok(
        "ta_signals",
        {
            "pair": data.get("pair", pair),
            "chain": chain,
            "hours": hours,
            "candles": data.get("candles", 0),
            "trend": data.get("trend"),
            "signal_count": len(sigs),
            "signals": sigs,
        },
        f"{data.get('pair', pair)}: {len(sigs)} signal(s), trend={data.get('trend')} over {hours}h",
    )


@mcp.tool()
async def ta_indicators(
    pair: str = Field(description="DEX pair / pool address."),
    hours: int = HOURS,
    chain: str = CHAIN,
) -> ToolResult:
    """Numeric indicators for one DEX pair: close, SMA20, EMA20, RSI14,
    volatility, drawdown and max drawdown over the requested hourly window."""
    if not pair.strip():
        return _refuse("ta_indicators", "invalid_argument", "pair must be a non-empty DEX pair address")
    if not _window_ok(hours):
        return _refuse("ta_indicators", "invalid_argument", "hours must be within 24..8760")
    try:
        data = _call("/api/v1/ta/indicators", {"chain": chain, "pair": pair.strip(), "hours": hours})
    except RuntimeError as exc:
        return _classify("ta_indicators", exc, pair, chain)
    keys = ("close", "sma20", "ema20", "rsi14", "volatility", "drawdown", "max_drawdown")
    return _ok(
        "ta_indicators",
        {
            "pair": data.get("pair", pair),
            "chain": chain,
            "hours": hours,
            "candles": data.get("candles", 0),
            **{k: data.get(k) for k in keys},
        },
        f"{data.get('pair', pair)}: RSI14={data.get('rsi14')} close={data.get('close')} ({data.get('candles', 0)} candles)",
    )


@mcp.tool()
async def market_odds(
    query: str = Field(description="Prediction-market question or topic, e.g. 'Bitcoin above 100k by December'."),
    limit: int = Field(default=10, ge=1, le=50, description="Maximum markets to return."),
) -> ToolResult:
    """Prediction-market odds and market questions from the ``prediction_markets``
    corpus lane — the retrieval shape used when odds are not on-chain rows."""
    q = query.strip()
    if not q:
        return _refuse("market_odds", "invalid_argument", "query must be a non-empty prediction-market question")
    try:
        resp = await _call_search_multi(query=q, collections=["prediction_markets"], limit=limit, rerank=True)
    except Exception as exc:
        return _refuse("market_odds", "upstream_error", str(exc)[:300], query=q)
    items = resp.get("results") or []
    if not items:
        return _refuse(
            "market_odds",
            "no_matches",
            "prediction_markets lane returned no markets for this query (lane may not be populated yet)",
            query=q,
        )
    return _ok(
        "market_odds",
        {
            "query": q,
            "count": len(items),
            "results": [
                {
                    "content": str(item.get("content", ""))[:1000],
                    "score": float(item.get("score", 0.0)),
                    "collection": item.get("collection") or item.get("source") or "prediction_markets",
                }
                for item in items
            ],
        },
        f"{len(items)} prediction market(s) for {q!r}",
    )
