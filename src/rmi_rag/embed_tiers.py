"""
Embedding Quality Tiers - Intelligent Provider Selection
=========================================================

Routes embedding requests to appropriate providers based on task type.
Saves premium (high-dim) credits for critical analysis. Uses cheap/free
providers for bulk ingestion and simple lookups.

Tiers:
  critical  - Scam detection, forensic reports, vulnerability mapping
              → 3072d Gemini only (highest quality for highest-stakes analysis)

  standard  - User searches, token profiles, wallet lookups
              → 1024-2048d NVIDIA/Mistral (good quality, free)

  light     - News ingestion, social feed, bulk data
              → 384d local BGE or hash (zero cost, unlimited)

  default   - Catch-all
              → Any available free provider

This ensures premium credits are preserved for the analysis that actually
benefits from 3072-dimension embeddings.
"""

# ──────────────────────────────────────────────────────────────
# Tier → Allowed Provider IDs
# ──────────────────────────────────────────────────────────────

TIER_PROVIDERS = {
    "critical": {"gemini_1", "gemini_2", "gemini_3"},  # 3072d only - highest quality
    "standard": {"nv_direct", "mistral", "nv_openrouter"},  # 1024-2048d - good quality, free
    "light": {"local_bge", "hash"},  # 384d - zero cost, unlimited
    "deep": {"gemini_1", "gemini_2", "gemini_3", "nv_openrouter"},  # 2048-3072d - deep analysis
    "default": set(),  # Any available provider (empty = all)
}

TIER_MIN_DIMS = {
    "critical": 2048,
    "standard": 768,
    "light": 0,
    "deep": 1024,
    "default": 0,
}

# ──────────────────────────────────────────────────────────────
# Task → Tier Mapping
# ──────────────────────────────────────────────────────────────

TASK_TIERS = {
    # ── Critical: needs high-dim embeddings for accuracy ──
    "scam_detection": "critical",
    "forensic_analysis": "critical",
    "vulnerability_mapping": "critical",
    "contract_audit": "critical",
    "deep_investigation": "deep",
    "rug_prediction": "deep",
    "campaign_detection": "deep",
    # ── Standard: good quality needed, but not critical ──
    "user_search": "standard",
    "token_profile": "standard",
    "wallet_lookup": "standard",
    "entity_resolution": "standard",
    "rag_citation": "standard",
    "scanner_enrichment": "standard",
    # ── Light: bulk, frequent, low-stakes ──
    "news_ingestion": "light",
    "social_feed": "light",
    "market_data": "light",
    "bulk_label_import": "light",
    "firehose_feed": "light",
    # ── Default ──
    "default": "default",
}


def get_tier(task: str) -> str:
    """Get the quality tier for a given task."""
    return TASK_TIERS.get(task, "default")


def get_allowed_providers(task: str) -> set[str]:
    """Get the set of allowed provider IDs for a task."""
    tier = get_tier(task)
    return TIER_PROVIDERS.get(tier, set())


def get_min_dims(task: str) -> int:
    """Get minimum dimensions required for a task."""
    tier = get_tier(task)
    return TIER_MIN_DIMS.get(tier, 0)


def is_provider_allowed(provider_id: str, task: str) -> bool:
    """Check if a provider is allowed for a given task."""
    allowed = get_allowed_providers(task)
    if not allowed:  # Empty set = all providers allowed
        return True
    return provider_id in allowed


def tier_summary() -> dict:
    """Return a summary of all tiers and their providers."""
    return {
        "tiers": {
            name: {
                "providers": list(providers) if providers else "all",
                "min_dims": TIER_MIN_DIMS.get(name, 0),
                "tasks": [t for t, tier in TASK_TIERS.items() if tier == name],
            }
            for name, providers in TIER_PROVIDERS.items()
        }
    }
