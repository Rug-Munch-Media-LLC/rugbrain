"""Collection boundary tables: what may be public, what is internal-only.

Pure data, split out of :mod:`rugbrain.knowledge_boundary` so that module holds
only the policy logic. These tables grow as collections are added, and data is
the wrong thing to spend a module's line budget on.
"""

from __future__ import annotations

from typing import Final

_PUBLIC = frozenset(
    {
        "audit_findings",
        "coding_knowledge",
        "contract_audits",
        "contract_security",
        "crypto_vulnerabilities",
        "defi_exploits",
        "education",
        "github_audits",
        "hacks_v2",
        "known_scams",
        "live_threats",
        "market_health",
        "news_v2",
        "public_crypto_patterns",
        "regulation_enforcement",
        "research_v2",
        "rmi_community",
        "scam_intel_v2",
        "scam_patterns",
        "security_alerts",
        "security_feeds",
        "social_intel",
        "solidity_patterns",
        "solana_patterns",
        "stablecoin_market",
        "threat_intel_v2",
        "wallet_profiles",
        "wallet_v2",
        "zk_circuits",
        "scam_signatures",
        # 2026-10-11: the _8b collections are the ones actually fed and served
        # (4096-dim, remote-8B embedder). The un-suffixed names above are the
        # pre-migration labels; leaving the boundary table on them alone made
        # every public search 403 "collection_not_available" against real data.
        # Both spellings stay allowed so a caller using either resolves.
        "codebase_reference_v2_8b",
        "cross_chain_8b",
        "crypto_vulnerabilities_8b",
        "defi_exploits_8b",
        "exploits_8b",
        "news_v2_8b",
        "protocol_design_8b",
        "regulation_enforcement_8b",
        "scam_intel_v2_8b",
        "scam_patterns_8b",
        "security_patterns_8b",
        "threat_intel_v2_8b",
        "wallet_v2_8b",
    }
)

_INTERNAL = frozenset(
    {
        "agent_vectors",
        "codebase_reference",
        "codebase_reference_v2",
        "master_skills",
        "protocol_design",
        "security_patterns",
        "security_patterns_test",
    }
)

_FORBIDDEN = frozenset(
    {
        "agent",
        "agent_registry",
        "backend",
        "bucket",
        "code_block",
        "data_source",
        "dispatch_prompt",
        "embedding",
        "embedding_model",
        "endpoint",
        "file_path",
        "glob_path",
        "host",
        "instructions",
        "internal",
        "library_id",
        "library_name",
        "master_skills",
        "model",
        "model_name",
        "path",
        "payload",
        "prefix",
        "project",
        "prompt",
        "provider",
        "qualified_name",
        "raw",
        "reason_why",
        "remote",
        "repo",
        "repository",
        "reranker_method",
        "routing",
        "secret",
        "served_by",
        "source_path",
        "source_kind",
        "system_prompt",
        "trace_backend",
        "vector",
        "vectors",
    }
)

PUBLIC_COLLECTIONS: Final[frozenset[str]] = _PUBLIC
INTERNAL_COLLECTIONS: Final[frozenset[str]] = _INTERNAL
FORBIDDEN_PUBLIC_KEYS: Final[frozenset[str]] = _FORBIDDEN
__all__ = ["FORBIDDEN_PUBLIC_KEYS", "INTERNAL_COLLECTIONS", "PUBLIC_COLLECTIONS"]
