"""
RAG Search Engine — shared constants and env-overridable defaults.

Collection routing tables, query-hint vocabularies, code-detection regexes
and the search-quality defaults used across the engine package. Kept free of
imports from sibling engine modules so every other module can depend on it.
"""

import os

# ═══════════════════════════════════════════════════════════════════
# SCAM / EXPLOIT KNOWLEDGE COLLECTIONS
# ═══════════════════════════════════════════════════════════════════
# Populated by scripts/ingest_scam_knowledge.py from public exploit/audit
# datasets (DeFiHackLabs, rekt-news, CryptoScamDB, SlowMist, SWC Registry).
# Queries about hacks/exploits/scams are routed here first so the RAG can
# reference specific incidents, techniques, and patterns from day one.
SCAM_COLLECTIONS: tuple[str, ...] = (
    "defi_exploits",
    "scam_patterns",
    "crypto_vulnerabilities",
    # Contract-knowledge expansion: known-good/bad contract code, security
    # patterns, and audit findings (populated by scripts/ingest_codebase_
    # knowledge.py, ingest_security_patterns.py, ingest_good_bad_contracts.py).
    "solidity_patterns",
    "solana_patterns",
    "contract_security",
    "audit_findings",
)

# ═══════════════════════════════════════════════════════════════════
# CODING-KNOWLEDGE COLLECTIONS
# ═══════════════════════════════════════════════════════════════════
# Populated by scripts/ingest_solidity_knowledge.py,
# scripts/ingest_rust_solana_knowledge.py, scripts/ingest_alt_vm_knowledge.py
# and scripts/ingest_security_guides.py. `coding_knowledge` carries
# language/framework/security-guide docs with code snippets; the pattern
# collections carry known-good/bad contract code. Code-intent queries
# ("how to implement X in Solidity", "Rust Solana program example",
# "secure ERC20") are routed here first so RugBrain answers as a
# smart-contract development expert.
CODING_COLLECTIONS: tuple[str, ...] = (
    "coding_knowledge",
    "solidity_patterns",
    "solana_patterns",
    "contract_security",
    "audit_findings",
)


# Content cap used for code-intent queries: fenced code blocks must not be
# truncated mid-block by the retrieval enrichment (default prose cap is 500).
_CODE_CONTENT_LIMIT = 8000
# Small similarity bonus applied to code-carrying chunks on code queries —
# enough to float a code chunk above equally-ranked prose, not enough to
# overturn a genuinely better semantic match.
_CODE_CHUNK_BONUS = 0.05

# ═══════════════════════════════════════════════════════════════════
# HYBRID RETRIEVAL (keyword/BM25 lane, additive)
# ═══════════════════════════════════════════════════════════════════
# Opt-in `hybrid=True` path: a lightweight in-memory Okapi BM25 lane runs
# alongside the vector search over the same Redis chunk corpus, and both
# lanes are fused with reciprocal rank fusion (RRF). Defaults are OFF so
# pure-vector behavior is preserved for existing callers.

# Per-collection doc cap for the keyword lane — keeps the per-query BM25
# index lightweight (tokenizing 1k chunks/collection is ~tens of ms).
_HYBRID_KEYWORD_MAX_DOCS = 1000
# Keyword hits fetched per collection relative to the requested limit.
_HYBRID_KEYWORD_LIMIT_FACTOR = 2
# Content preview attached to keyword-only hits (surfaces without vector hit).
_HYBRID_CONTENT_PREVIEW = 500


# ═══════════════════════════════════════════════════════════════════
# SEARCH-QUALITY DEFAULTS (env-overridable)
# ═══════════════════════════════════════════════════════════════════


def _env_bool(name: str, default: bool) -> bool:
    """Parse a 0/1/true/false/yes/no/on/off env var into a bool."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Reranking is ON by default on the main search entry points: the top
# ``rerank_top_k`` fused candidates are re-scored with the self-hosted Qwen3 cross-encoder
# (Qwen3-Reranker-4B) before truncation. The search-lane reranker degrades
# gracefully — when no model backend is installed, or scoring takes longer
# than ``rerank_timeout``, results fall back to their original retrieval
# order with ``rerank_method="none"`` / ``reranked=false`` — a request never
# fails because the reranker is missing or slow.
_RERANK_DEFAULT = _env_bool("RAG_RERANK_ENABLED", True)
# Reranker timeout (seconds). <=0 disables the timeout (score everything).
_RERANK_TIMEOUT = float(os.getenv("RAG_RERANK_TIMEOUT", "2.0"))
# Lightweight curated synonym/alias expansion (default OFF — additive recall;
# see ``rugbrain.search.query_transform.expand_synonyms``). Flip on globally
# with ``RAG_SYNONYM_EXPAND=1`` or per-call with ``synonym_expand=True``.
_SYNONYM_EXPAND_DEFAULT = _env_bool("RAG_SYNONYM_EXPAND", False)
# MMR diversity lambda: 1.0 = pure relevance, 0.0 = pure diversity. 0.6 leans
# relevance while still de-duplicating near-duplicate chunks — deliberately
# NOT over-deduping gold-relevant docs (the eval precision headroom comes from
# the reranker, not from stronger MMR diversity).
_MMR_LAMBDA_DEFAULT = float(os.getenv("RAG_MMR_LAMBDA", "0.6"))
