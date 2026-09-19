"""Firehose configuration — collection caps, quality floor, rate limits.

NOTE: there is intentionally NO backend RAG_API here. RugBrain ingests
directly into its local Qdrant store (docs/MODERNIZATION.md §5).
"""

# Per-collection size caps (auto-evict oldest on overflow)
COLLECTION_CAPS = {
    "known_scams": 50000,
    "scam_patterns": 5000,
    "forensic_reports": 10000,
    "contract_audits": 5000,
    "wallet_profiles": 100000,
    "news_articles": 20000,
    "market_intel": 5000,
    "token_analysis": 50000,
    "transaction_patterns": 10000,
    "social_sentiment": 10000,
    "general": 10000,
}

# Quality thresholds (skip docs below this score)
MIN_QUALITY_SCORE = 30  # 0-100

# Rate limits (docs per minute per collection)
RATE_LIMITS = {
    "known_scams": 60,
    "news_articles": 30,
    "social_sentiment": 20,
    "default": 30,
}

__all__ = ["COLLECTION_CAPS", "MIN_QUALITY_SCORE", "RATE_LIMITS"]
