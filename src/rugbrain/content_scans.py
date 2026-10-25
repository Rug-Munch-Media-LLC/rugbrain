"""Content scans — scan-of-interest drafts from real scanner feedback.

Split out of :mod:`rugbrain.content_engine` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("rugbrain.content")

SCANNER_BASE = os.getenv("RMI_SCANNER_BASE_URL", "https://rugmunch.io/scan")

MAX_MAIN_PER_CYCLE = int(os.getenv("RAG_CONTENT_MAX_MAIN", "3"))


async def select_scans(limit: int = 3) -> list[dict[str, Any]]:
    """Scans-of-interest drafts from REAL scanner feedback docs."""
    from rugbrain.qdrant_store import get_qdrant_store

    store = await get_qdrant_store()
    drafts: list[dict[str, Any]] = []
    try:
        points, _ = await store.scroll("scanner_feedback", limit=limit * 4)
    except Exception as e:
        logger.debug("scan_scroll_failed err=%s", str(e)[:80])
        return []

    picked = 0
    for p in points:
        payload = p.get("payload", {}) or {}
        md = payload.get("metadata", {}) or {}
        token = md.get("token_address") or md.get("token") or ""
        chain = md.get("chain", "")
        risk = md.get("risk_score")
        if not token or risk is None:
            continue
        reasons = md.get("reasons") or md.get("red_flags") or []
        reason_lines = "\n".join(
            f"  • {r}" for r in (reasons if isinstance(reasons, list) else [str(reasons)])[:4]
        )
        ref_link = (
            f"{SCANNER_BASE}?token={token}&chain={chain}"
            if chain
            else f"{SCANNER_BASE}?token={token}"
        )
        text = (
            f"[SCAN] {chain.upper() if chain else 'token'} · {token[:10]}…{token[-6:]}\n"
            f"\nRisk score: {risk}/100 ({md.get('risk_level', 'n/a')})"
            + (f"\nWhy:\n{reason_lines}" if reason_lines else "")
            + f"\n\nFull scan output: {ref_link}"
        )
        drafts.append(
            {
                "text": text,
                "story_ref": f"scanner_feedback:{token[:12]}",
                "mirror": False,
                "kind": "scan",
                "ref_link": ref_link,
            }
        )
        picked += 1
        if picked >= limit:
            break
    return drafts
