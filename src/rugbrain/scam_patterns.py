"""Scam pattern extraction from ingested threat intel.

Turns raw ingested exploit/hack data (defihacklabs PoCs, SWC registry,
rekt-news reports -- all already in the ``threat_intel_v2`` Qdrant collection)
into structured, queryable scam patterns::

    {
        "pattern_id": "reentrancy:access-control",
        "attack_type": "reentrancy",
        "technique": "withdraw-after-external-call",
        "code_snippet": "...",
        "affected_protocols": ["Agave", "..."],
        "chain": "EVM",
        "severity": "critical",
        "funds_lost": 1_500_000,
        "reference_incidents": ["https://...", "..."],
        "indicators": ["call.value", "withdraw(...)"],
    }

The extractor is deterministic (no LLM) so it can run on every ingest
without cost; an optional LLM enrichment pass (``pattern_extractor``)
can be layered on top later. Patterns are stored in the ``scam_patterns``
Qdrant collection and cached in Redis for fast lookups.

This module is the public facade. The work is split by responsibility
(size ratchet, 2026-09-29):

* :mod:`scam_vocab`     -- the canonical vocabularies (data only).
* :mod:`scam_detect`    -- deterministic keyword detection.
* :mod:`scam_fields`    -- per-field parsing (funds lost, severity).
* :mod:`scam_aggregate` -- docs -> grouped patterns (pure).
* :mod:`scam_store`     -- Qdrant/Redis read, cache and persist.
* :mod:`scam_profile`   -- raw scanner token data -> normalized profile.
"""

from __future__ import annotations

from rugbrain.scam_aggregate import extract_scam_patterns_from_docs
from rugbrain.scam_detect import detect_attack_vectors, detect_indicators, normalize_vector
from rugbrain.scam_fields import max_severity, parse_funds_lost
from rugbrain.scam_profile import build_token_scam_profile
from rugbrain.scam_store import (
    PATTERN_CACHE_KEY,
    PATTERN_CACHE_TTL,
    SCAM_PATTERNS_COLLECTION,
    THREAT_INTEL_COLLECTION,
    extract_scam_patterns_from_intel,
    get_scam_patterns,
    store_scam_patterns,
)
from rugbrain.scam_vocab import ATTACK_VECTORS, INDICATOR_KEYWORDS, VECTOR_KEYWORDS

# The implementation is public now; the underscore alias is kept because the
# BUSD-ticker regression suite imports this exact name, and that regression is
# worth more than the tidiness of the rename. Removing the alias would break a
# test that guards a real \$15-trillion misparse.
_parse_funds_lost = parse_funds_lost
_max_severity = max_severity

# Every name in __all__ is bound by an import statement above this line, so
# __all__ cannot name a phantom -- the failure mode that let a split leave
# `recall_from_gains` in a list with no definition behind it.
__all__ = [
    "ATTACK_VECTORS",
    "INDICATOR_KEYWORDS",
    "PATTERN_CACHE_KEY",
    "PATTERN_CACHE_TTL",
    "SCAM_PATTERNS_COLLECTION",
    "THREAT_INTEL_COLLECTION",
    "VECTOR_KEYWORDS",
    "build_token_scam_profile",
    "detect_attack_vectors",
    "detect_indicators",
    "extract_scam_patterns_from_docs",
    "extract_scam_patterns_from_intel",
    "get_scam_patterns",
    "normalize_vector",
    "store_scam_patterns",
]
