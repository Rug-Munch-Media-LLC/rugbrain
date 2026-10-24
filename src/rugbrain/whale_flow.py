"""Whale-flow signal — a bounded projection over the wallet label corpora.

The gap this closes: the market surface has no whale/flow read at all. M7
("whale flows") and M8 ("exchange net flows") in
``docs/CAPABILITY-GAPS-SCAM-MARKET.md`` are marked ⚠️ — ``smart_money_tracker.py``
tracks 4 hardcoded wallets and ``exchange_flow.py`` has no write path, so
neither produces a queryable signal. The ``exchange_flows`` collection is an
empty 1024-dim shell (retired 2026-10-15, see
``docs/DECISION-EMPTY-MARKET-COLLECTIONS.md``).

This module does NOT fetch and does NOT embed. It is a PURE projection over
rows the existing wallet corpora already hold:

  * ``wallet_v2_8b``       — 10,278 labelled addresses (malicious / exploiter /
                             mixer / exchange), source ``etherscan_malicious``
                             + ``etherscan_combined``.
  * ``wallet_profiles_8b`` — 40,355 labelled addresses (defi / dex / cex /
                             bridge / unknown), source ``etherscan_combined``.

The signal is deliberately bounded: it answers "what does the label corpus say
about the flow-relevant population" — how many exchange, mixer, exploiter and
whale-class addresses we hold, and which of them are flagged — rather than
pretending to a live netflow number we cannot compute from labels alone.

The classification rule is stated here rather than buried in a constant,
because the output is a claim about real addresses:

  * ``exchange``  — a CEX hot/deposit wallet (label or name matches a known
                    exchange). Funds moving here are "off-market".
  * ``mixer``     — a privacy-protocol address (Tornado/Railgun/Aztec). A flow
                    through a mixer is a laundering signal.
  * ``exploiter`` — an address labelled as an attacker. A flow from here is
                    tainted.
  * ``whale``     — a large-holder / foundation / VC address. A flow from here
                    is "smart money".
  * ``other``     — labelled but none of the above.

A row with no recognisable label is ``other``, NOT ``whale``: "we have never
classified this address" is a different claim from "this is a whale", and only
the first is supportable from this data.
"""

from __future__ import annotations

from typing import Any

from rugbrain.whale_flow_text import _haystack, _text

__all__ = [
    "EXCHANGE_HINTS",
    "MIXER_HINTS",
    "WHALE_HINTS",
    "classify_address",
    "whale_flow_summary",
]

#: Substrings (lowercased) that mark a CEX hot/deposit wallet.
EXCHANGE_HINTS: tuple[str, ...] = (
    "binance",
    "coinbase",
    "kraken",
    "bybit",
    "okx",
    "gate.io",
    "gate ",
    "kucoin",
    "huobi",
    "htx",
    "bitfinex",
    "gemini",
    "crypto.com",
    "cex",
    "exchange",
)

#: Substrings that mark a privacy-protocol (mixer) address.
MIXER_HINTS: tuple[str, ...] = (
    "tornado",
    "railgun",
    "aztec",
    "mixer",
    "sanctioned",
)

#: Substrings that mark a large-holder / smart-money address.
WHALE_HINTS: tuple[str, ...] = (
    "whale",
    "foundation",
    "treasury",
    "capital",
    "ventures",
    "fund",
    "jump",
    "wintermute",
    "cumberland",
    "alameda",
)

#: Entity types that are flow-relevant on their own (from the label corpus).
_FLOW_ENTITY_TYPES = frozenset({"malicious", "exploiter", "mixer", "exchange"})




def classify_address(row: dict[str, Any]) -> str:
    """Classify one labelled-address row into a flow bucket.

    Order matters: an address that is BOTH a mixer and an exchange is a mixer
    (the stronger laundering signal wins). An address labelled ``exploiter`` or
    ``malicious`` is an exploiter regardless of any name hint.
    """
    hay = _haystack(row)
    entity = _text(row.get("entity_type") or (row.get("metadata") or {}).get("entity_type")).lower()

    if entity in {"exploiter", "malicious"} or "exploiter" in hay or "phishing" in hay:
        return "exploiter"
    if entity == "mixer" or any(h in hay for h in MIXER_HINTS):
        return "mixer"
    if entity == "exchange" or any(h in hay for h in EXCHANGE_HINTS):
        return "exchange"
    if any(h in hay for h in WHALE_HINTS):
        return "whale"
    return "other"


def whale_flow_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise a batch of labelled-address rows into a flow signal.

    *rows* are Qdrant payloads (or payload-shaped dicts) from ``wallet_v2_8b``
    / ``wallet_profiles_8b``. Returns a bounded, auditable summary:

    ``counts``      — addresses per bucket (exchange / mixer / exploiter /
                      whale / other).
    ``total``       — rows seen.
    ``flow_risk``   — 0..1, the share of flow-relevant addresses that are
                      tainted (exploiter + mixer) over all classified
                      addresses. 0 when nothing is classified.
    ``risk_level``  — ``low`` / ``medium`` / ``high`` / ``critical`` from
                      ``flow_risk``.
    ``flagged``     — the tainted addresses (exploiter + mixer), deduped,
                      capped at ``limit``, each with its bucket and label.

    The rule is stated, not magic: ``flow_risk`` is the tainted share of the
    classified population, so a corpus with no tainted addresses reads 0.0 and
    a corpus that is half tainted reads 0.5.
    """
    counts: dict[str, int] = {"exchange": 0, "mixer": 0, "exploiter": 0, "whale": 0, "other": 0}
    flagged: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in rows:
        bucket = classify_address(row)
        counts[bucket] += 1
        if bucket in {"exploiter", "mixer"}:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            addr = _text(row.get("address") or meta.get("address"))
            if addr and addr not in seen:
                seen.add(addr)
                flagged.append(
                    {
                        "address": addr,
                        "bucket": bucket,
                        "label": _text(row.get("label") or meta.get("label")),
                    }
                )

    total = sum(counts.values())
    tainted = counts["exploiter"] + counts["mixer"]
    flow_risk = round(tainted / total, 4) if total else 0.0

    if flow_risk >= 0.5:
        risk_level = "critical"
    elif flow_risk >= 0.25:
        risk_level = "high"
    elif flow_risk >= 0.1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "counts": counts,
        "total": total,
        "flow_risk": flow_risk,
        "risk_level": risk_level,
        "flagged": flagged[:20],
    }
