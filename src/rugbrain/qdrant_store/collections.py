"""Collection registry, ownership write-gate and interim namespace routing."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
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
        # Ingest targets that are absent from Qdrant entirely, so their sources
        # failed a THIRD time even once the collections were created
        # (2026-09-28). Same class as the August finding above, same root
        # cause: a source's DEFAULT_COLLECTION and this owned-set are two
        # declarations of the same fact, kept in two places, and they drift.
        #
        # Each name below is the declared target of a rugbrain source:
        #   l2_risk            <- ingest_sources/l2beat.py
        #   zk_circuits        <- ingest_sources/zk.py
        #   mev_architecture   <- ingest_sources/mev.py
        #   bridge_architecture<- ingest_sources/bridges.py
        #   privacy_protocol   <- ingest_sources/privacy.py
        #   standards          <- ingest_sources/eips.py
        #   market_data        <- ingest_sources/defillama.py
        #   crime_reports      <- ingest_sources/chainabuse.py
        #
        # None of these is a ContextCrypto collection, so adding them widens
        # nothing the gate exists to protect against. The gate's purpose is to
        # stop rugbrain writing into the OTHER repo's data; these are this
        # repo's own outputs.
        "l2_risk",
        "zk_circuits",
        "mev_architecture",
        "bridge_architecture",
        "privacy_protocol",
        "standards",
        "market_data",
        "crime_reports",
        # 2026-09-28: targets of three built sources that were writing into
        # the void. rug_events <- crpwarner_rug_events.py; scam_signatures <-
        # forta_malicious_contracts.py + fraudcoins_signatures.py. Same
        # DEFAULT_COLLECTION-vs-owned-set drift as the 8 added above.
        "rug_events",
        "scam_signatures",
        # 2026-09-28: target of scripts/ingest_scam_patterns.py (Lane B,
        # commit d17b4fa). Deliberately a NEW name: scam_patterns already holds
        # 7,102 cryptoscamdb PER-VICTIM records, and these are 90 AGGREGATED
        # attack-pattern signatures. Operator rule is that nothing is overwritten,
        # so the two meanings must never share a collection. Both stay.
        "scam_pattern_signatures_8b",
        # Explicitly reviewed, redacted ContextCrypto projection only.
        # Never add the internal ContextCrypto collections to this gate.
        "public_crypto_patterns",
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
        "news_v2",
        "news_articles",
        "social_intel",
        "known_scams",
        "scam_patterns",
        "scam_intel_v2",
        "wallet_profiles",
        "wallet_v2",
        "defi_exploits",
        "threat_intel_v2",
        "contract_audits",
        "crypto_vulnerabilities",
        "regulation_enforcement",
        "research_v2",
        "education",
        "github_audits",
        "contract_security",
        "security_feeds",
        "market_health",
        "stablecoin_market",
        "live_threats",
        "hacks_v2",
    }
)


def unified_route(collection: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Map a consolidated collection to (rag_documents, provenance filter).

    Returns the collection unchanged (and no filter) when unified reads are
    disabled or the name is not consolidated.
    """
    if UNIFIED_READS and collection in CONSOLIDATED_SOURCES:
        return UNIFIED_COLLECTION, {"key": "collection", "match": {"value": collection}}
    return _map_collection(collection), None


def dense_vector_dimensions(params: Mapping[str, Any]) -> set[int]:
    """Return configured dense vector dimensions from a Qdrant params object.

    Qdrant uses either a legacy bare-vector object (``{"size": N}``) or a
    named-vector object (``{"dense": {"size": N}}``).  Keeping this parsing in
    one place prevents readers and writers from disagreeing about a schema.
    """
    vectors = params.get("vectors")
    if not isinstance(vectors, dict):
        return set()
    size = vectors.get("size")
    if isinstance(size, int):
        return {size}
    dimensions: set[int] = set()
    for spec in vectors.values():
        if isinstance(spec, dict) and isinstance(spec.get("size"), int):
            dimensions.add(int(spec["size"]))
    return dimensions


_existing_collections: dict[str, int] = {}


async def _ensure_collection(
    collection: str,
    vector_size: int,
    client: httpx.AsyncClient,
    *,
    create_if_missing: bool = True,
) -> bool:
    """Ensure a collection exists and can accept the requested vector dimension.

    Read-only callers can disable auto-creation so a missing canary target
    fails closed instead of being materialized as an empty shadow collection.
    """
    if vector_size <= 0:
        logger.error(
            "qdrant_collection_invalid_dim collection=%s requested_dim=%d",
            collection,
            vector_size,
        )
        return False
    if _existing_collections.get(collection) == vector_size:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
            configured_dims = dense_vector_dimensions(params)
            if configured_dims and vector_size not in configured_dims:
                logger.error(
                    "qdrant_collection_incompatible collection=%s requested_dim=%d configured_dims=%s",
                    collection,
                    vector_size,
                    sorted(configured_dims),
                )
                return False
            _existing_collections[collection] = vector_size
            return True
        if r.status_code != 404:
            logger.error(
                "qdrant_collection_probe_failed collection=%s status=%d",
                collection,
                r.status_code,
            )
            return False
    except Exception as e:
        logger.warning("qdrant_collection_probe_failed collection=%s err=%s", collection, e)
        return False

    if not create_if_missing:
        logger.warning(
            "qdrant_collection_missing collection=%s reason=create_if_missing_false",
            collection,
        )
        return False

    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections[collection] = vector_size
        return ok
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False
