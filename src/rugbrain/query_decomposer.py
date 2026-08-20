"""Multi-hop Query Decomposition — breaks complex queries into sequential sub-queries.

A complex crypto intelligence query like "Did the exploiter of the Radiant
Capital hack move funds to Tornado Cash?" gets decomposed into:

1. ``search("Radiant Capital hack exploit address")``
2. ``trace(exploit_address, "fund flow")``
3. ``check(tornado_cash_interaction)``
4. ``synthesize(hops_1_2_3)``

Each sub-query can be dispatched to a different search strategy (semantic,
blockchain, entity), and results are aggregated into a final answer.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Decomposition patterns: regex → (query_type, sub_queries)
DECOMPOSITION_PATTERNS: list[tuple[re.Pattern[str], str, list[list[str]]]] = [
    # Fund flow tracing: "where did the stolen funds go?"
    (
        re.compile(
            r"(stolen|hack|exploit|drain|theft|stole|rob).*(fund|money|eth|usdc|usdt|token|asset)"
        ),
        "fund_flow",
        [
            ["identify_incident", "Find the blockchain incident and exploit address"],
            ["trace_funds", "Follow outbound transactions from the exploit address"],
            ["check_mixers", "Check if funds were sent to a mixer/tumbler"],
            ["check_cex", "Check if funds were deposited to a centralized exchange"],
            ["synthesize", "Combine findings into a fund flow narrative"],
        ],
    ),
    # Token risk assessment: "is this token safe?"
    (
        re.compile(
            r"(safe|risk|scam|rug|honeypot|legit|trust).{0,20}(token|coin|meme|alt)"
            r"|(token|coin|meme|alt).{0,20}(safe|risk|scam|rug|honeypot|legit|trust)"
        ),
        "token_risk",
        [
            ["scan_contract", "Run a security scan on the token contract"],
            ["check_holders", "Analyze holder distribution for concentration risk"],
            ["check_liquidity", "Check liquidity pool health and lock status"],
            ["check_team", "Look up deployer wallet history"],
            ["synthesize", "Combine findings into a risk assessment"],
        ],
    ),
    # Whale activity: "what is this whale doing?"
    (
        re.compile(r"(whale|large|big).*(move|transfer|buy|sell|accumulate|dump)"),
        "whale_activity",
        [
            ["identify_wallet", "Identify the whale wallet address"],
            ["analyze_portfolio", "Analyze the wallet's current portfolio"],
            ["track_recent_tx", "Track recent significant transactions"],
            ["check_pattern", "Check for accumulation or distribution patterns"],
            ["synthesize", "Combine findings into a whale activity summary"],
        ],
    ),
    # Protocol/pool analysis: "is this pool safe?"
    (
        re.compile(r"(pool|liquidity|farm|vault|strategy|yield).*(safe|risk|apy|return)"),
        "protocol_analysis",
        [
            ["analyze_pool", "Analyze the liquidity pool composition"],
            ["check_audit", "Check if the protocol has been audited"],
            ["check_tvl", "Check total value locked and trends"],
            ["check_risk", "Check for known vulnerabilities in the protocol"],
            ["synthesize", "Combine findings into a protocol analysis"],
        ],
    ),
    # Cross-chain bridge: "what happened to this bridge?"
    (
        re.compile(r"(bridge|cross|chain).*(hack|exploit|drain|attack|vuln)"),
        "bridge_analysis",
        [
            ["identify_bridge", "Identify the bridge and its contracts"],
            ["check_chain", "Check all connected chains for anomalies"],
            ["trace_flow", "Trace cross-chain fund flows"],
            ["check_validators", "Check validator/oracle set for compromise"],
            ["synthesize", "Combine findings into a bridge analysis"],
        ],
    ),
]


def classify_query(query: str) -> dict[str, Any]:
    """Classify a user query into a query type and decomposition plan.

    Args:
        query: The user's natural language query.

    Returns:
        Dict with ``query_type``, ``sub_queries`` list, ``original_query``,
        and ``confidence`` (0.0-1.0) of the classification.
    """
    query_lower = query.lower()

    for pattern, query_type, sub_queries in DECOMPOSITION_PATTERNS:
        match = pattern.search(query_lower)
        if match:
            return {
                "query_type": query_type,
                "sub_queries": [
                    {"id": step_id, "prompt": prompt} for step_id, prompt in sub_queries
                ],
                "original_query": query,
                "confidence": 0.8,
                "matched_pattern": match.group(),
            }

    # Default: single-hop search
    return {
        "query_type": "simple_search",
        "sub_queries": [
            {"id": "search", "prompt": f"Search for: {query}"},
        ],
        "original_query": query,
        "confidence": 0.5,
        "matched_pattern": None,
    }


def decompose_query(
    query: str,
    tools_available: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Break a user query into sequential sub-queries for dispatch.

    Args:
        query: The user's natural language query.
        tools_available: Optional list of available tool names to filter
                         sub-queries against (e.g., ``["search", "trace",
                         "check_cex"]``).

    Returns:
        List of sub-query dicts, each with ``id``, ``prompt``, and
        optionally ``tool`` (if tools_available is provided).
    """
    classification = classify_query(query)
    sub_queries: list[dict[str, Any]] = list(classification["sub_queries"])

    if tools_available:
        # Filter sub-queries to only use available tools
        # Map sub_query IDs to tool names
        tool_map = {
            "search": "search",
            "identify_incident": "search",
            "trace_funds": "trace",
            "check_mixers": "check_chain",
            "check_cex": "check_offchain",
            "scan_contract": "scan",
            "check_holders": "analyze",
            "check_liquidity": "analyze",
            "check_team": "trace",
            "identify_wallet": "search",
            "analyze_portfolio": "analyze",
            "track_recent_tx": "trace",
            "check_pattern": "analyze",
            "analyze_pool": "analyze",
            "check_audit": "search",
            "check_tvl": "analyze",
            "check_risk": "search",
            "identify_bridge": "search",
            "check_chain": "analyze",
            "trace_flow": "trace",
            "check_validators": "analyze",
            "synthesize": "synthesize",
        }

        for sq in sub_queries:
            sq["tool"] = tool_map.get(sq["id"], "search")

    return sub_queries
