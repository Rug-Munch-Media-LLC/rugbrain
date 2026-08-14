"""DeFiLlama open API source — live-context market data helper.

Source: https://api.llama.fi (no API key; generous rate limits).

Provides:

  * ``fetch_tvl_chains`` / ``tvl_to_docs``  — TVL snapshot by chain
    (https://api.llama.fi/v2/chains).
  * ``fetch_protocol`` / ``protocol_to_doc`` — per-protocol TVL + revenue
    context (https://api.llama.fi/protocol/{slug}).
  * ``fetch_hacks`` / ``hacks_to_docs``     — structured hack incidents
    (https://api.llama.fi/hacks) — the maintained replacement for the dead
    Rekt RSS feed.

The ``DeFiLlamaSource`` returns TVL-snapshot docs (doc_type ``market_data``)
so it feeds the market-context search path; the hacks/protocol helpers are
exposed for threat-intel and protocol-analysis pipelines. All parsers are
pure and tolerate missing fields (the API shape evolves).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

HACKS_URL = "https://api.llama.fi/hacks"
TVL_CHAINS_URL = "https://api.llama.fi/v2/chains"
PROTOCOL_URL = "https://api.llama.fi/protocol/{slug}"
DEFAULT_COLLECTION = "market_data"
DOC_TYPE = "market_data"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _iso_date(ts: Any) -> str:
    try:
        ts_f = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if ts_f <= 0:
        return ""
    return datetime.fromtimestamp(ts_f, tz=UTC).date().isoformat()


def _chain_name(chain: Any) -> str:
    if isinstance(chain, list) and chain:
        return str(chain[0]).lower()
    return str(chain or "unknown").lower()


# ── TVL snapshot (chains) ────────────────────────────────────────────────


async def fetch_tvl_chains(
    client: httpx.AsyncClient, limit: int | None = None
) -> list[dict[str, Any]]:
    """Fetch the per-chain TVL snapshot (``/v2/chains``); [] on failure."""
    try:
        r = await client.get(TVL_CHAINS_URL)
        if r.status_code != 200:
            log.warning("defillama_tvl_status status=%d", r.status_code)
            return []
        data = r.json()
    except Exception as e:
        log.warning("defillama_tvl_error err=%s", str(e)[:200])
        return []
    chains = data if isinstance(data, list) else []
    if limit is not None:
        chains = chains[:limit]
    return chains


def tvl_to_docs(chains: list[Any], limit: int | None = None) -> list[IngestDoc]:
    """Normalize the per-chain TVL snapshot into market_data docs."""
    docs: list[IngestDoc] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        name = str(chain.get("name") or chain.get("gecko_id") or chain.get("symbol") or "").strip()
        if not name:
            continue
        tvl = float(chain.get("tvl") or 0)
        change = chain.get("change_24h") or chain.get("tvlChange") or 0
        content = (
            f"DeFiLlama TVL snapshot — {name}.\n"
            f"Total value locked: ${tvl:,.0f}\n"
            f"24h change: {change}\n"
        )
        docs.append(
            IngestDoc(
                content=content,
                doc_id=f"defillama:tvl:{name.lower()}:{_content_hash(content)}",
                doc_type=DOC_TYPE,
                source="defillama",
                title=f"{name} TVL",
                url="https://defillama.com/chains",
                metadata={
                    "collection": DEFAULT_COLLECTION,
                    "chain": name.lower(),
                    "tvl_usd": tvl,
                    "change_24h": change,
                    "source_url": "https://defillama.com/chains",
                    "doc_type": DOC_TYPE,
                },
            )
        )
    if limit is not None:
        docs = docs[:limit]
    return docs


# ── Per-protocol TVL/revenue context ─────────────────────────────────────


async def fetch_protocol(client: httpx.AsyncClient, slug: str) -> dict[str, Any] | None:
    """Fetch one protocol's TVL/revenue context; None on failure."""
    try:
        r = await client.get(PROTOCOL_URL.format(slug=slug))
        if r.status_code != 200:
            log.debug("defillama_protocol_status slug=%s status=%d", slug, r.status_code)
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("defillama_protocol_error slug=%s err=%s", slug, str(e)[:200])
        return None


def protocol_to_doc(proto: dict[str, Any]) -> IngestDoc | None:
    """Normalize a protocol object into a market_data doc (TVL + revenue)."""
    name = str(proto.get("name") or proto.get("slug") or "").strip()
    if not name:
        return None
    tvl = float(proto.get("tvl") or 0)
    revenue = proto.get("revenue") or proto.get("revenue_24h") or 0
    chain_tvls = proto.get("chainTvls") or {}
    top_chains = sorted(
        ((str(k), float(v or 0)) for k, v in chain_tvls.items() if isinstance(v, (int, float))),
        key=lambda kv: kv[1],
        reverse=True,
    )[:3]
    chain_line = ", ".join(f"{k}: ${v:,.0f}" for k, v in top_chains) or "unknown"
    content = (
        f"DeFiLlama protocol snapshot — {name}.\n"
        f"TVL: ${tvl:,.0f}\n"
        f"Revenue: {revenue}\n"
        f"Chain TVL breakdown: {chain_line}\n"
    )
    return IngestDoc(
        content=content,
        doc_id=f"defillama:protocol:{name.lower()}:{_content_hash(content)}",
        doc_type=DOC_TYPE,
        source="defillama",
        title=f"{name} (protocol)",
        url=f"https://defillama.com/protocol/{proto.get('slug', name)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": name,
            "slug": proto.get("slug", ""),
            "tvl_usd": tvl,
            "revenue": revenue,
            "top_chains": [c for c, _ in top_chains],
            "source_url": f"https://defillama.com/protocol/{proto.get('slug', name)}",
            "doc_type": DOC_TYPE,
        },
    )


# ── Hacks (structured incidents) ─────────────────────────────────────────


async def fetch_hacks(client: httpx.AsyncClient, limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch the DeFiLlama hacks database; [] on failure."""
    try:
        r = await client.get(HACKS_URL)
        if r.status_code != 200:
            log.warning("defillama_hacks_status status=%d", r.status_code)
            return []
        data = r.json()
    except Exception as e:
        log.warning("defillama_hacks_error err=%s", str(e)[:200])
        return []
    hacks = data if isinstance(data, list) else []
    if limit is not None:
        hacks = hacks[:limit]
    return hacks


def hacks_to_docs(hacks: list[Any], limit: int | None = None) -> list[IngestDoc]:
    """Normalize DeFiLlama hack incidents into market_data/incident docs."""
    docs: list[IngestDoc] = []
    for hack in hacks:
        if not isinstance(hack, dict):
            continue
        name = str(hack.get("name") or "").strip() or "unknown"
        date_iso = _iso_date(hack.get("date"))
        amount = float(hack.get("amount") or 0) or None
        technique = str(hack.get("technique") or "").strip()
        classification = str(hack.get("classification") or "").strip()
        chain = _chain_name(hack.get("chain"))
        returned = hack.get("returnedFunds")
        content = f"DeFi hack incident — {name}.\nDate: {date_iso or 'unknown'}\n"
        content += f"Funds lost: ${amount:,.0f}\n" if amount else "Funds lost: unknown\n"
        if technique:
            content += f"Technique: {technique}\n"
        if classification:
            content += f"Classification: {classification}\n"
        content += f"Chain: {chain}\n"
        if returned is not None:
            content += f"Funds returned: {returned}\n"
        doc_id_base = f"defillama:hacks:{name.lower()}:{date_iso or amount or 0}"
        docs.append(
            IngestDoc(
                content=content,
                doc_id=f"{doc_id_base}:{_content_hash(content)}",
                doc_type="post_mortem",
                source="defillama",
                title=f"DeFi hack: {name}",
                url="https://defillama.com/hacks",
                metadata={
                    "collection": "threat_intel",
                    "protocol": name,
                    "date": date_iso,
                    "date_ts": float(hack.get("date") or 0) or None,
                    "amount_lost_usd": amount,
                    "chain": chain,
                    "technique": technique or None,
                    "classification": classification or None,
                    "returned_funds": returned,
                    "source_type": "defillama_hacks",
                    "source_url": "https://defillama.com/hacks",
                    "doc_type": "post_mortem",
                },
            )
        )
    if limit is not None:
        docs = docs[:limit]
    return docs


class DeFiLlamaSource(Source):
    """DeFiLlama open API — live market context (TVL snapshot)."""

    name = "defillama"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "DeFiLlama open API — TVL/protocol revenue market context (live)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        chains = await fetch_tvl_chains(client, limit)
        return tvl_to_docs(chains, limit)


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "HACKS_URL",
    "PROTOCOL_URL",
    "TVL_CHAINS_URL",
    "DeFiLlamaSource",
    "fetch_hacks",
    "fetch_protocol",
    "fetch_tvl_chains",
    "hacks_to_docs",
    "protocol_to_doc",
    "tvl_to_docs",
]
