"""Collection registry, ownership write-gate and interim namespace routing."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Collection management ─────────────────────────────────────────────

# Collections owned by rugbrain (write-allowed when mode=readonly).
# rmi-backend owns rmi_documents — rugbrain must never write there.
#
# NOTE (2026-08-16): the migration to hybrid `*_v2` collections
# (scripts/migrate_to_sparse.py) renamed the RAG collections
# (threat_intel → threat_intel_v2, hacks → hacks_v2, …) and NO Qdrant
# aliases exist, so the old names must NOT be used — writing to them
# auto-creates an empty fragment. The list below reflects the real
# collections the RAG ingest pipelines write to (per
# docs/COLLECTION_REGISTRY.md). All writers use *_v2 names — no legacy
# collection is owned anymore.
_RMI_RAG_OWNED_COLLECTIONS: frozenset[str] = frozenset(
    {
        # Hybrid-migrated RAG collections (real names — no aliases exist).
        "threat_intel_v2",
        "defi_exploits",
        "known_scams",
        "scam_intel_v2",
        "crime_reports",
        "contract_audits",
        "crypto_vulnerabilities",
        "hacks_v2",
        "news_v2",
        "research_v2",
        "wallet_v2",
        "scanner_v2",
        "security_feeds",
        "security_alerts",
        "regulation_enforcement",
        # Legacy collections still written by continuous ingest jobs.
        "news_articles",
        "education",
        "coding_knowledge",
        "contract_security",
        "solidity_patterns",
        "solana_patterns",
        "stablecoin_market",
        "security_patterns",
        "cryptography",
        "github_audits",
        "live_threats",
        # RAG-owned auxiliary / graph collections.
        "rag_threats",
        "rag_docs",
        "wallet_clusters",
        "bundle_patterns",
        "cluster_labels",
        "wallet_profiles",
        # Media/content corpus written by rugbrain's own ingest sources
        # (youtube.py, newsletters.py, podcasts.py). These were missing from
        # the registry, so the readonly write gate silently blocked their
        # ingesters (found 2026-08-24 while wiring the interim namespace).
        "youtube_videos",
        "newsletters",
        "podcast_episodes",
    }
)

_RMI_RAG_QDRANT_WRITE_MODE = os.getenv("RMI_RAG_QDRANT_WRITE_MODE", "readonly").lower()

# ── Interim collection namespace (2026-08-24 migration window) ─────────────
# During the v1→v2 re-embedding migration the active embedder may produce a
# different dimension than the canonical collections (free NVIDIA nemotron
# 2048-dim vs qwen3-embedding-4b 2560-dim). Vectors from different models
# must NEVER mix (see _resize / TOP-100 #25), so while
# RAG_INTERIM_COLLECTION_SUFFIX is set, ALL reads and writes route to a
# suffixed shadow namespace auto-created at the live embedder's native dim.
# Remove the env var once rag-embed-migration.service completes, then restart.
_INTERIM_SUFFIX = os.getenv("RAG_INTERIM_COLLECTION_SUFFIX", "")


def _map_collection(collection: str | None) -> str | None:
    """Route to the interim shadow namespace when one is configured."""
    if collection and _INTERIM_SUFFIX and not collection.endswith(_INTERIM_SUFFIX):
        return f"{collection}{_INTERIM_SUFFIX}"
    return collection


# ── Unified reads (2026-09-20 consolidation) ───────────────────────────────
# Every legacy RAG collection was copied into ``rag_documents`` with the
# source recorded in the ``collection`` payload field (content-hash doc ids,
# 334k points). With RAG_UNIFIED_READS=1 reads for a consolidated collection
# are served from the unified store with an equivalent payload filter — the
# legacy collections stay intact until retirement, so this is reversible by
# flipping one env var.
UNIFIED_COLLECTION = "rag_documents"
UNIFIED_READS = os.getenv("RAG_UNIFIED_READS", "0").lower() in ("1", "true", "yes")

CONSOLIDATED_SOURCES: frozenset[str] = frozenset(
    {
        "news_v2", "news_articles", "social_intel", "known_scams", "scam_patterns",
        "scam_intel_v2", "wallet_profiles", "wallet_v2", "defi_exploits",
        "threat_intel_v2", "contract_audits", "crypto_vulnerabilities",
        "regulation_enforcement", "research_v2", "education", "github_audits",
        "contract_security", "security_feeds", "market_health", "stablecoin_market",
        "live_threats", "hacks_v2",
    }
)


def unified_route(collection: str | None) -> tuple[str | None, dict | None]:
    """Map a consolidated collection to (rag_documents, provenance filter).

    Returns the collection unchanged (and no filter) when unified reads are
    disabled or the name is not consolidated.
    """
    if UNIFIED_READS and collection in CONSOLIDATED_SOURCES:
        return UNIFIED_COLLECTION, {"key": "collection", "match": {"value": collection}}
    return _map_collection(collection), None


_existing_collections: set[str] = set()


async def _ensure_collection(collection: str, vector_size: int, client: httpx.AsyncClient) -> bool:
    if collection in _existing_collections:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            _existing_collections.add(collection)
            return True
    except Exception as e:
        logger.debug("qdrant_collection_probe_failed collection=%s err=%s", collection, e)
    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections.add(collection)
        return ok
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False
