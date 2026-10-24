"""The facts of write ownership: which corpora this repo may write to.

Pure data. No routing, no canonicalisation, no policy — those live in
``ownership`` (the gate) and ``interim`` (the namespace). Split from them so a
reviewer can read the whole ownership question ("which names, and why is this
one here?") without wading through routing code.

THE CLASS OF DEFECT THE COMMENTS BELOW RECORD
    A source's ``DEFAULT_COLLECTION`` and this set are two declarations of the
    same fact, kept in two places, and they drift. Every 2026-08/2026-09 entry
    below was a source whose declared target was MISSING here, so the readonly
    write gate silently blocked an ingester that was working fine. The comment
    on each group names the source, because the name is the only way to find
    the other half of the pair again.
"""

from __future__ import annotations

import os
from typing import Final

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
_RMI_RAG_OWNED_COLLECTIONS: Final[frozenset[str]] = frozenset(
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
        "gmgn_docs",
        "wallet_v2",
        "scanner_v2",
        # Live scanner output. Was absent: the twin did not exist, so the write
        # path had nothing to resolve to and every scan landed on the retired
        # 2560 base while scan_intel_8b stayed at 0 (measured 2026-09-30, 97
        # collections). The corpus is owned either way — the base has held
        # 60,483 points of it — so the gate must admit it now that the twin
        # exists, or resolve_collection would return a name the gate refuses.
        "scan_intel",
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
        # Social / market-sentiment corpora (operator 2026-12: social intel lives
        # in RugBrain, not in the backend). These are written by RugBrain's own
        # HF ingests (financial tweets + labelled sentiment) and read by the
        # market-specialist MCP surface, so they belong to this repo.
        "social_intel",
        # Operator directive 2026-10-12 ("RugBrain is its own boss"): the
        # remaining RugBrain-written corpora that were missing from this gate.
        # scam_patterns alone blocked the shill-watchlist ingester; the rest are
        # the market-data and intel targets this repo already writes.
        "scam_patterns",
        "exploits",
        "cross_chain",
        "protocol_design",
        # RETIRED 2026-10-15: exchange_flows + liquidation_events are 1024-dim
        # empty shells with no source that writes them. They stay in the owned
        # set so the write gate still recognises the names if a future source is
        # built, but nothing advertises them. See
        # docs/DECISION-EMPTY-MARKET-COLLECTIONS.md.
        "exchange_flows",
        "market_health",
        "liquidation_events",
        "hyperliquid",
        "token_unlocks",
        "scanned_tokens",
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

#: ``readonly`` = write only owned corpora (the gate's normal mode);
#: ``owner`` = full write access (legacy behaviour).
_RMI_RAG_QDRANT_WRITE_MODE: Final[str] = os.getenv("RMI_RAG_QDRANT_WRITE_MODE", "readonly").lower()

__all__: Final = ["_RMI_RAG_OWNED_COLLECTIONS", "_RMI_RAG_QDRANT_WRITE_MODE"]
