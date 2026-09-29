"""Vertical instruction library for the search-lane cross-encoder.

Qwen3-Reranker is instruction-aware (official docs: +1-5% retrieval; FollowIR
14.84 = best-in-class instruction following). Our llama.cpp build IGNORES the
``/rerank`` 'instruct' field (hardcoded generic web-search instruct), so task
framing is embedded into the query text instead — validated live 2026-08-22:
a Tornado-Cash-funding evidence doc went 0.001 -> 0.99 on a rug-pull query.

The enable flag is read at CALL time, not at import, so
``importlib.reload(rugbrain.search.rerank_instructions)`` and a
``RERANK_INSTRUCTIONS=off`` environment both take effect for a process that
already imported the module.
"""

from __future__ import annotations

import os

INSTRUCTION_LIBRARY: dict[str, str] = {
    "forensics": (
        "Crypto forensics task: rank documents by their evidentiary value for "
        "assessing rug-pull and scam risk, including funding sources, wallet "
        "concentration, mint authority, liquidity lock status, and honeypot signals."
    ),
    "contract": (
        "Smart-contract security review task: rank documents by relevance to "
        "contract analysis, prioritizing exploit patterns, privileged functions, "
        "audit findings, and vulnerability disclosures."
    ),
    "tracing": (
        "Fund-tracing task: rank documents by relevance to tracing fund flows, "
        "wallet attribution, mixer usage, bridge hops, and entity resolution."
    ),
    "code": (
        "Blockchain code intelligence task: rank documents by relevance to the "
        "development question, prioritizing Solidity/Solana security semantics "
        "and concrete vulnerable patterns or fixes."
    ),
}

VERTICAL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "forensics",
        (
            "rug",
            "rugpull",
            "rug pull",
            "scam",
            "honeypot",
            "deployer",
            "mint authority",
            "liquidity lock",
            "slippage",
            "pump and dump",
            "fraud",
            "steal",
            "drain",
        ),
    ),
    (
        "tracing",
        (
            "trace",
            "fund flow",
            "mixer",
            "tornado",
            "attribution",
            "launder",
            "bridge hop",
            "cluster",
            "entity resolution",
            "source of funds",
        ),
    ),
    (
        "contract",
        (
            "contract",
            "solidity",
            "solana program",
            "audit",
            "reentrancy",
            "overflow",
            "access control",
            "proxy",
            "upgradeable",
            "token standard",
        ),
    ),
    (
        "code",
        (
            "code",
            "function",
            "snippet",
            "implement",
            "example",
            "anchor",
            "web3.py",
            "ethers",
            "sdk",
            "how do i",
            "how to",
        ),
    ),
)


def instructions_enabled() -> bool:
    """True unless ``RERANK_INSTRUCTIONS=off`` (read fresh, never cached)."""
    return os.getenv("RERANK_INSTRUCTIONS", "true").lower() != "off"


def detect_vertical(query: str) -> str | None:
    """Classify a query into a platform vertical for instruction selection.

    Order matters: forensics > tracing > contract > code. Returns None when
    no vertical matches (generic/news queries keep the model's default
    web-search instruction, which is optimal for plain relevance).
    """
    q = query.lower()
    for vertical, patterns in VERTICAL_PATTERNS:
        if any(p in q for p in patterns):
            return vertical
    return None


def apply_instruction(query: str) -> str:
    """Embed the vertical instruction into the query text (query-slot trick).

    Returns the query unchanged when instructions are disabled or no vertical
    matches. The constant per-vertical prefix is also prompt-prefix-cache
    friendly on the llama.cpp side.
    """
    if not instructions_enabled():
        return query
    vertical = detect_vertical(query)
    if vertical is None:
        return query
    return f"[TASK] {INSTRUCTION_LIBRARY[vertical]} [QUERY] {query}"
