"""Collection registry, ownership write-gate and interim namespace routing."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx

from rugbrain.qdrant_store.lane_resolver import (
    resolve_collection,
    strip_twin_suffix,
)

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
        # rag_documents is this repo's own UNIFIED corpus — every legacy RAG
        # collection was consolidated into it (530k points, 2026-09-20) and it is
        # the read target of the whole `RAG_UNIFIED_READS` route plus the
        # guardrail examples in `rugbrain.guardrails`. It was missing from this
        # gate, so the gate rejected writes to the corpus the repo itself
        # creates and reads. This is the same class of defect as the entries
        # added above — a target this repo owns, absent from the owned set — and
        # it is what made the lane-resolver write-gate test fail: resolving
        # `rag_documents` to its populated `rag_documents_8b` twin must be
        # admissible, otherwise the fix trades a dim mismatch for a gate block.
        # rmi-backend does not own this collection, so the gate's purpose is
        # unaffected.
        "rag_documents",
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
        # 2026-09-28: targets of three built sources that were writing into
        # the void. rug_events <- crpwarner_rug_events.py; scam_signatures <-
        # forta_malicious_contracts.py + fraudcoins_signatures.py. Same
        # DEFAULT_COLLECTION-vs-owned-set drift as the 8 added above.
        # (crime_reports is already listed in the group above, from
        # ingest_sources/chainabuse.py — it was repeated here and ruff B033
        # flagged the duplicate set item.)
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


# ── Canonical-lane resolution (2026-09-28) ─────────────────────────────────
# ``_map_collection`` is the single choke point every read, has_point, scroll and
# insert passes through, so the base -> ``_8b`` rewrite is applied here once
# rather than in each caller. Order matters: the interim suffix is applied
# OUTERMOST, so a name arriving as ``threat_intel_v2`` becomes
# ``threat_intel_v2_8b`` first and the interim shadow then wraps that
# (``threat_intel_v2_8b<interim>``). A name arriving as
# ``threat_intel_v2<interim>`` is left alone by the resolver (it does not end in
# ``_8b`` and is not a declared base) and is only shadow-routed, which preserves
# the existing interim behaviour exactly.
#
# A name absent from the declared lane table is NOT rewritten and NOT rejected:
# it passes through, and ``declared_lane_dim`` reports None for it. A caller
# that knows its name must resolve calls ``lane_resolver.require_resolved``.
# See lane_resolver for the live-Qdrant evidence behind the mapping.


def _map_collection(collection: str | None) -> str | None:
    """Route a declared name to the live target: interim shadow, then lane.

    The interim suffix is read from the PACKAGE namespace at call time, not from
    this module's global, for the reason the package docstring gives: tests patch
    ``rugbrain.qdrant_store._INTERIM_SUFFIX`` and internal callers resolve shared
    names through the package. Reading the local global here would leave the
    patch invisible — the same split-namespace hazard the pre-existing gate code
    avoided by taking the suffix as a parameter.
    """
    import rugbrain.qdrant_store as _pkg

    resolved = resolve_collection(collection)
    interim = _pkg._INTERIM_SUFFIX
    if resolved and interim and not resolved.endswith(interim):
        return f"{resolved}{interim}"
    return resolved


def gate_collection_name(
    collection: str | None,
    interim_suffix: str | None = None,
) -> str | None:
    """Canonical base name for the write gate, stripping BOTH suffixes.

    Ownership is a property of the corpus, not of the lane or the migration
    window: ``threat_intel_v2_8b<interim>``, ``threat_intel_v2_8b`` and
    ``threat_intel_v2`` are the same owned corpus, so all three must present
    ``threat_intel_v2`` to the gate. Returns None for a None name.

    *interim_suffix* is passed in rather than read from this module's global so
    the caller stays the single patch point: tests patch the PACKAGE attribute
    (``qdrant_store._INTERIM_SUFFIX``), and internal callers resolve shared
    names through the package namespace at call time. Reading the module global
    here would silently ignore those patches.
    """
    if not collection:
        return None
    base = collection
    if interim_suffix and base.endswith(interim_suffix):
        base = base[: -len(interim_suffix)]
    return strip_twin_suffix(base)


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
