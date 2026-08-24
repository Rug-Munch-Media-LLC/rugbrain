"""Trending shill-scam detection — velocity methodology over RAG corpus.

The "guaranteed" signal (2026-08-24): a token being shilled shows up as
ACCELERATING mentions across independent sources within short windows.
Single-channel pumps are noise; N distinct channels/accounts echoing the
same ticker/CA with rising per-hour velocity is the campaign signature.

Method:
    1. Pull recent docs (default last 6h) from intel collections.
    2. Extract tickers ($ABC) and contract addresses per doc, bucket by hour.
    3. Score each token: distinct_sources * velocity_slope * severity_weight
       - velocity_slope = (mentions in last 2h) - (mentions 2-4h ago) —
         acceleration beats raw volume (catches campaigns EARLY).
    4. Tier output:
         CRITICAL  ≥8 sources accelerating   → push main channel
         HIGH      ≥4 sources accelerating   → alerts channel
         WATCH     ≥2 sources, positive slope → ingest-only tag

Runs as part of the ingestion cycle (cheap: pure corpus analysis, no
external calls) and exposes results via /api/v1/content/trending.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger("rugbrain.trending")

TICKER_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,15}\b")
CA_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
HOUR_BUCKET = 3600


def _epoch_of(doc: dict[str, Any]) -> float:
    ts = (doc.get("metadata") or {}).get("ts") or doc.get("published") or ""
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return time.time()


def score_trending(docs: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """Velocity-score tokens across the provided doc list."""
    now = now or time.time()
    # token -> {source_set, hourly_counts{bucket: n}, sample, cas}
    data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"sources": set(), "hours": defaultdict(int), "sample": "", "cas": set()}
    )
    for d in docs:
        text = d.get("content", "")
        src = d.get("source", "")
        ep = _epoch_of(d)
        bucket = int((now - ep) // HOUR_BUCKET) if ep <= now else 0
        for tok in TICKER_RE.findall(text):
            k = tok.upper()
            rec = data[k]
            rec["sources"].add(src)
            rec["hours"][bucket] += 1
            if not rec["sample"]:
                rec["sample"] = text[:300]
        for ca in CA_RE.findall(text):
            data[ca[:10] + "…"]["cas"].add(ca)

    out: list[dict[str, Any]] = []
    for tok, rec in data.items():
        h = rec["hours"]
        recent = sum(h.get(b, 0) for b in (0, 1))  # last 2h
        prior = sum(h.get(b, 0) for b in (2, 3))  # 2–4h ago
        slope = recent - prior
        n_src = len(rec["sources"])
        if n_src < 2 or (recent == 0 and prior == 0):
            continue
        score = round(n_src * 2 + max(slope, 0) * 1.5 + min(len(rec["cas"]), 5), 2)
        if n_src >= 8 and slope > 0:
            tier = "CRITICAL"
        elif n_src >= 4 and slope > 0:
            tier = "HIGH"
        else:
            tier = "WATCH"
        out.append(
            {
                "token": tok,
                "tier": tier,
                "score": score,
                "distinct_sources": n_src,
                "mentions_last_2h": recent,
                "mentions_prior_2h": prior,
                "velocity_slope": slope,
                "contracts": sorted(rec["cas"])[:3],
                "sample": rec["sample"],
            }
        )
    out.sort(key=lambda x: (-{"CRITICAL": 0, "HIGH": 1, "WATCH": 2}.get(x["tier"], 3), -x["score"]))
    return out


async def collect_recent_docs(hours: int = 6, per_collection: int = 200) -> list[dict[str, Any]]:
    """Scroll recent docs from the interim intel collections."""
    from rugbrain.qdrant_store import get_qdrant_store

    store = await get_qdrant_store()
    docs: list[dict[str, Any]] = []
    for coll in ("news_v2_i", "threat_intel_v2_i"):
        try:
            points, _ = await store.scroll(coll, limit=per_collection)
        except Exception as e:
            logger.debug("trend_scroll_failed coll=%s err=%s", coll, str(e)[:80])
            continue
        for p in points:
            pl = p.get("payload", {}) or {}
            docs.append(
                {
                    "content": pl.get("content", ""),
                    "source": pl.get("source", ""),
                    "metadata": pl.get("metadata", {}),
                    "published": pl.get("metadata", {}).get("ts", ""),
                }
            )
    return docs


async def trending_report() -> dict[str, Any]:
    docs = await collect_recent_docs()
    tiers = score_trending(docs)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "docs_analyzed": len(docs),
        "critical": [t for t in tiers if t["tier"] == "CRITICAL"],
        "high": [t for t in tiers if t["tier"] == "HIGH"],
        "watch": [t for t in tiers if t["tier"] == "WATCH"][:10],
    }
