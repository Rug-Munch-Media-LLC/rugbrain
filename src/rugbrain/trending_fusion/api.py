"""Public envelope of the one trending engine.

``GET /api/v1/trending?chain=&limit=`` and the MCP tool
``rugbrain_trending_fused(chain, limit)`` both return exactly what
:func:`rugbrain_trending_fused` returns: the frozen contract, or a typed
refusal. An empty ``ranked`` list is never returned -- a chain with no
candidates refuses and says so.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from rugbrain.trending_fusion.chains import CHAINS, resolve_chain
from rugbrain.trending_fusion.sources import trending_fused

ENGINE = "rugbrain.trending_fusion/v1"
DEFAULT_LIMIT = 10
NON_SOURCE_KEYS = {"discovery_scope"}


def _money(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "0"


def _refusal(source: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "source": source, "reason": reason}


def _item(row: Mapping[str, Any]) -> dict[str, Any]:
    promotions = row.get("disclosed_promotions") or []
    evidence = [
        {
            "source": e.get("source"),
            "signal": e.get("signal"),
            "value": e.get("value"),
            "paid": bool(e.get("paid")),
        }
        for e in row.get("evidence") or []
    ]
    return {
        "rank": row.get("rank"),
        "chain": row.get("chain"),
        "token_address": row.get("token_address"),
        "symbol": row.get("symbol") or "",
        "score": row.get("score"),
        "evidence": evidence,
        "paid": bool(promotions),
        "paid_only": bool(row.get("paid_only")),
        "disclosures": sorted({f"{p.get('kind') or 'paid'}${_money(p.get('amount_usd'))}" for p in promotions}),
    }


async def rugbrain_trending_fused(chain: str = "solana", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Rank one chain through the shared scorer. Never returns an empty ``ranked``."""
    spec = resolve_chain(chain)
    if spec is None:
        supported = ",".join(sorted(CHAINS))
        return _refusal("chain_registry", f"unsupported_chain:{chain}; supported={supported}")
    size = limit if isinstance(limit, int) and limit > 0 else DEFAULT_LIMIT
    result = await trending_fused(spec.name, limit=size)
    ranked = result.get("ranked") or []
    if not ranked:
        detail = (result.get("missing_sources") or {}).get("discovery_scope") or "no_candidates"
        return _refusal(spec.data_source, f"no_rankable_candidates:{detail}")
    missing = result.get("missing_sources") or {}
    return {
        "chain": spec.name,
        "engine": ENGINE,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ranked": [_item(row) for row in ranked],
        "paid_only_excluded": [
            {"token_address": p.get("token_address"), "reason": "paid_only"}
            for p in result.get("paid_only_excluded") or []
        ],
        "missing_sources": sorted(k for k in missing if k not in NON_SOURCE_KEYS),
        "stale": any("failed" in str(v) for v in missing.values()),
    }
