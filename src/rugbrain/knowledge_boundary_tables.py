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
