"""Historical cross-reference — what happened to tokens like this one?

Given a token address, find similar historical tokens and surface what
happened to them (funds lost, category, time-to-rug), so a user scanning a
new token sees a concrete precedent: *"this token looks like AgaveExploit
(reentrancy) which lost ~$1.5M in 2022."*

Two similarity backends, tried in order:

1. **LanceDB via lite-indexer** (``lite_indexer.vectors.LanceVectorStore``,
   ``token_embeddings`` collection) when the lite-indexer package and a
   populated LanceDB directory are available. The lite-indexer indexes
   on-chain tokens with a local embedder, so this gives true
   bytecode/behavior similarity for live chain data.
2. **Qdrant fallback** — hybrid (dense+BM25) search across the RAG's
   ``threat_intel_v2`` and ``scams_v2`` collections for docs that mention the
   address or match its semantic fingerprint.

Both backends fail open: if neither is available the module returns an
empty result with a ``"backend": "none"`` note instead of raising.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from rugbrain.qdrant_store import QdrantVectorStore

logger = logging.getLogger(__name__)

# Env override for the LanceDB directory (default matches lite-indexer).
LANCE_DIR = os.getenv("LANCE_DIR", "/var/data/lance")
# Optional path to the lite-indexer repo so the LanceVectorStore can be
# imported without installing the package into the RAG image.
LITE_INDEXER_PATH = os.getenv("LITE_INDEXER_PATH", "")

_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")


# ── LanceDB backend ────────────────────────────────────────────────────


def _import_lance_store() -> Any | None:
    """Import the lite-indexer LanceVectorStore, or None when unavailable.

    Tries the installed ``lite_indexer`` package first, then falls back to
    importing from ``LITE_INDEXER_PATH`` (a repo checkout). Returns None on
    any failure so callers degrade to the Qdrant fallback.
    """
    try:
        from lite_indexer.vectors.lance_store import (
            LanceVectorStore,
        )

        return LanceVectorStore
    except Exception as e:
        logger.debug("lancedb lite_indexer import failed: %s", e)

    if LITE_INDEXER_PATH:
        import sys

        candidate = os.path.join(LITE_INDEXER_PATH, "lite_indexer")
        if os.path.isdir(candidate):
            sys.path.insert(0, LITE_INDEXER_PATH)
            try:
                from lite_indexer.vectors.lance_store import LanceVectorStore

                return LanceVectorStore
            except Exception as e:
                logger.debug("lancedb path import failed: %s", e)
                sys.path.remove(LITE_INDEXER_PATH)
    return None


async def _lancedb_similar(
    address: str,
    chain: str,
    limit: int,
) -> list[dict[str, Any]] | None:
    """Return similar historical tokens from LanceDB, or None when unavailable."""
    try:
        store_cls = _import_lance_store()
        if store_cls is None:
            return None
        store = store_cls(path=LANCE_DIR)
        try:
            rows = await store.similar_by_id("token_embeddings", address.lower(), k=limit)
        except Exception as e:
            logger.debug("lancedb token_embeddings lookup failed: %s", e)
            return None
        finally:
            close = getattr(store, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("lancedb store close failed", exc_info=True)

        results: list[dict[str, Any]] = []
        for row in rows or []:
            distance = row.get("_distance")
            results.append(
                {
                    "address": row.get("id") or address,
                    "chain": row.get("chain") or chain,
                    "symbol": row.get("symbol") or "",
                    "name": row.get("name") or row.get("text") or "",
                    "similarity": round(1.0 - (distance or 0.0), 4)
                    if distance is not None
                    else 1.0,
                    "outcome": row.get("outcome") or "",
                    "funds_lost_usd": row.get("funds_lost_usd") or 0,
                    "category": row.get("category") or "",
                    "date": row.get("date") or "",
                    "backend": "lancedb",
                }
            )
        return results
    except Exception as e:
        logger.debug("lancedb backend failed: %s", e)
        return None


# ── Qdrant fallback ────────────────────────────────────────────────────


async def _qdrant_similar(
    address: str,
    chain: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Find historical threat-intel docs related to *address* via Qdrant.

    Runs hybrid (dense + BM25) search on ``threat_intel_v2`` and ``scams_v2``
    with the address as the query, then merges and dedupes. Returns an
    empty list when Qdrant or the embedder is unavailable.
    """
    from rugbrain.embeddings import get_embedding

    store = QdrantVectorStore()
    results: list[dict[str, Any]] = []
    try:
        try:
            embedding = await get_embedding(f"scam token address {address} {chain}")
        except Exception as e:
            logger.debug("cross_reference embed failed: %s", e)
            return []

        query = f"{address} {chain} scam rug hack exploit"
        for collection in ("threat_intel_v2", "scams_v2"):
            try:
                hits = await store.hybrid_search(
                    query_text=query,
                    query_embedding=embedding,
                    collection=collection,
                    limit=limit,
                    min_similarity=0.3,
                )
            except Exception as e:
                logger.debug("qdrant %s search failed: %s", collection, e)
                continue
            for hit in hits:
                metadata = hit.get("metadata", {}) or {}
                content = hit.get("content", "") or ""
                results.append(
                    {
                        "address": address,
                        "chain": metadata.get("chain") or chain,
                        "name": metadata.get("contract_name")
                        or metadata.get("protocol")
                        or metadata.get("token_name")
                        or "",
                        "symbol": metadata.get("token_symbol") or "",
                        "similarity": hit.get("similarity", 0.0),
                        "outcome": _outcome_from_metadata(metadata),
                        "funds_lost_usd": _funds_from_metadata(metadata),
                        "category": metadata.get("category") or metadata.get("attack_vector") or "",
                        "date": metadata.get("date") or metadata.get("attack_date") or "",
                        "content_excerpt": content[:300],
                        "source": hit.get("source", ""),
                        "source_url": metadata.get("source_url", ""),
                        "backend": "qdrant",
                    }
                )
    finally:
        await store.close()

    # Dedupe by source_url/content, keep highest similarity.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in sorted(results, key=lambda x: x.get("similarity", 0), reverse=True):
        key = r.get("source_url") or r.get("content_excerpt", "")[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
        if len(unique) >= limit:
            break
    return unique


def _funds_from_metadata(metadata: dict[str, Any]) -> float:
    """Extract a USD funds-lost figure from threat-intel metadata."""
    raw = metadata.get("amount_lost_usd")
    if raw is None:
        raw = metadata.get("amount_lost")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    # String like "$1.5M"
    match = re.search(r"([\d,\.]+)\s*([KMB])?", str(raw), re.IGNORECASE)
    if not match:
        return 0.0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    suffix = (match.group(2) or "").upper()
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return number * multiplier


def _outcome_from_metadata(metadata: dict[str, Any]) -> str:
    """Derive a human-readable outcome string from threat-intel metadata."""
    category = metadata.get("category") or metadata.get("attack_vector") or ""
    funds = _funds_from_metadata(metadata)
    if funds > 0:
        return f"exploited — lost ~${funds:,.0f}"
    if category:
        return f"flagged as {category}"
    return "flagged in threat intel"


# ── Orchestrators ──────────────────────────────────────────────────────


async def find_similar_historical_tokens(
    address: str,
    chain: str = "ethereum",
    limit: int = 10,
) -> dict[str, Any]:
    """Find historical tokens similar to *address*.

    Tries LanceDB first, falls back to Qdrant, and returns a normalized
    result::

        {"address", "chain", "limit", "backend", "similar_tokens": [...]}
    """
    address = address.strip().lower()
    if not _ADDRESS_RE.fullmatch(address):
        return {
            "address": address,
            "chain": chain,
            "limit": limit,
            "backend": "none",
            "similar_tokens": [],
            "error": "invalid_address",
        }

    lancedb_hits = await _lancedb_similar(address, chain, limit)
    if lancedb_hits is not None:
        return {
            "address": address,
            "chain": chain,
            "limit": limit,
            "backend": "lancedb",
            "similar_tokens": lancedb_hits[:limit],
        }

    qdrant_hits = await _qdrant_similar(address, chain, limit)
    if qdrant_hits:
        return {
            "address": address,
            "chain": chain,
            "limit": limit,
            "backend": "qdrant",
            "similar_tokens": qdrant_hits[:limit],
        }

    return {
        "address": address,
        "chain": chain,
        "limit": limit,
        "backend": "none",
        "similar_tokens": [],
    }


def build_narrative(
    address: str,
    similar_tokens: list[dict[str, Any]],
    matched_patterns: list[dict[str, Any]] | None = None,
) -> str:
    """Build a "this token looks like X" narrative from similar tokens.

    Uses the strongest matched pattern and the most similar historical
    token to produce a concise, evidence-backed sentence.
    """
    if not similar_tokens:
        return (
            f"No historical token closely resembles {address} in the current index. "
            "Treat as unknown until on-chain analysis completes."
        )

    best = max(similar_tokens, key=lambda t: t.get("similarity", 0))
    name = best.get("name") or best.get("address", "a known token")
    outcome = best.get("outcome") or "flagged in threat intel"
    similarity = best.get("similarity", 0.0)

    if matched_patterns:
        top = max(matched_patterns, key=lambda m: m.get("confidence", 0))
        technique = top.get("technique") or top.get("name", "")
        conf = top.get("confidence", 0)
        narrative = (
            f"{address} matches the {technique} pattern ({conf:.0%} confidence) and "
            f"resembles {name} ({similarity:.0%} similarity), which was {outcome}."
        )
    else:
        narrative = (
            f"{address} resembles {name} ({similarity:.0%} similarity), which was {outcome}."
        )

    # Add a time-to-rug note when the historical token carries a date.
    if best.get("date"):
        try:
            dt = datetime.fromisoformat(str(best["date"]).replace("Z", "+00:00"))
            days_ago = (datetime.now(UTC) - dt).days
            if days_ago > 0:
                narrative += f" That incident occurred {days_ago} days ago."
        except (ValueError, TypeError):
            pass

    return narrative


async def cross_reference_token(
    address: str,
    chain: str = "ethereum",
    limit: int = 10,
    matched_patterns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full historical cross-reference for a token address.

    Combines similar-token discovery with the pattern-matching verdict into
    one response::

        {"address", "chain", "backend", "similar_tokens", "narrative"}
    """
    similar = await find_similar_historical_tokens(address, chain, limit)
    narrative = build_narrative(
        address,
        similar.get("similar_tokens", []),
        matched_patterns=matched_patterns,
    )
    return {
        "address": address,
        "chain": chain,
        "backend": similar.get("backend", "none"),
        "similar_tokens": similar.get("similar_tokens", []),
        "narrative": narrative,
    }


__all__ = [
    "build_narrative",
    "cross_reference_token",
    "find_similar_historical_tokens",
]
