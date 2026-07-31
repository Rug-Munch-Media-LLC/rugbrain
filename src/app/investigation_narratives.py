"""Investigation Narratives — agentic multi-hop forensic tracing.

Self-contained port of rmi-backend ``app.investigation_narratives`` for
the rmi-rag standalone container.

"Follow the money from this scam token 5 hops → tell me the story."
Combines multi-hop RAG retrieval and narrative generation to produce
human-readable investigation reports.

Pipeline:
  1. Pick investigation template by type (rug_pull / honeypot / phishing / general)
  2. Execute each hop via rmi-rag's Qdrant vector store
  3. Accumulate evidence across hops
  4. LLM synthesizes narrative (graceful degraded output if no LLM key)
  5. Return structured report with confidence scoring
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Investigation templates ──────────────────────────────────────

HOP_TEMPLATES = {
    "rug_pull": [
        {
            "hop": "scam_identification",
            "goal": "Identify the scam token: deployer, creation time, initial liquidity",
        },
        {
            "hop": "deployer_trace",
            "goal": "Trace the deployer: other tokens deployed, wallet age, funding source",
        },
        {
            "hop": "funding_flow",
            "goal": "Follow the money: where did deployer get funds? CEX, mixer, or other scams?",
        },
        {
            "hop": "liquidity_exit",
            "goal": "Trace where the liquidity went after the rug: recipient wallets, DEX swaps",
        },
        {
            "hop": "cross_chain_escape",
            "goal": "Check if funds were bridged to other chains (Solana, BSC, Arbitrum, etc.)",
        },
    ],
    "honeypot": [
        {
            "hop": "contract_analysis",
            "goal": "Check contract for transfer restrictions, blacklist, trading pause",
        },
        {
            "hop": "deployer_history",
            "goal": "Has deployer created other honeypots? Check behavioral fingerprint",
        },
        {
            "hop": "victim_identification",
            "goal": "Find wallets that bought but couldn't sell - estimate losses",
        },
    ],
    "phishing": [
        {
            "hop": "site_analysis",
            "goal": "Identify the phishing site: domain age, hosting, similar domains",
        },
        {"hop": "wallet_drain", "goal": "Trace the drainer wallet: where did stolen funds go?"},
        {
            "hop": "drainer_network",
            "goal": "Is this drainer part of a known phishing ring? Check wallet clusters",
        },
    ],
    "general": [
        {
            "hop": "entity_lookup",
            "goal": "Find all known information about the target address/token",
        },
        {
            "hop": "transaction_analysis",
            "goal": "Analyze recent transactions: patterns, counterparties, unusual activity",
        },
        {
            "hop": "risk_assessment",
            "goal": "Synthesize risk signals from all available data sources",
        },
    ],
}

DEFAULT_COLLECTIONS = ["threat_intel", "rmi_agent_memory", "rmi_community"]


async def _execute_hop(
    hop: dict[str, str],
    query: str,
    collections: list[str],
) -> dict[str, Any]:
    """Execute one investigation hop via rmi-rag Qdrant search."""
    hop_query = f"{query} {hop.get('goal', '')}"
    hop_name = hop.get("hop", "unknown")

    try:
        from rmi_rag.embeddings import get_embedding
        from rmi_rag.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(hop_query)
        store = QdrantVectorStore()
        all_results: list[dict[str, Any]] = []
        try:
            for coll in collections:
                try:
                    hits = await store.search(
                        query_embedding=embedding,
                        collection=coll,
                        limit=10,
                        min_similarity=0.3,
                    )
                    for h in hits:
                        h["collection"] = coll
                    all_results.extend(hits)
                except Exception as exc:
                    logger.debug("hop_search_coll_failed coll=%s err=%s", coll, str(exc)[:120])
        finally:
            await store.close()

        # Best-first, deduplicated
        seen_ids = set()
        unique: list[dict[str, Any]] = []
        for r in sorted(all_results, key=lambda x: x.get("similarity", 0), reverse=True):
            rid = r.get("id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            unique.append(r)

        return {
            "hop": hop_name,
            "goal": hop.get("goal", ""),
            "query": hop_query,
            "results_count": len(unique),
            "results": unique[:5],
            "confidence": {
                "score": int(max((r.get("similarity", 0) for r in unique), default=0) * 100)
            },
            "success": len(unique) > 0,
        }
    except Exception as exc:
        logger.warning("hop_failed hop=%s err=%s", hop_name, str(exc)[:200])
        return {
            "hop": hop_name,
            "goal": hop.get("goal", ""),
            "error": str(exc),
            "success": False,
        }


async def _synthesize_narrative(
    query: str,
    hops: list[dict[str, Any]],
    cross_chain_matches: dict[str, Any] | None = None,
) -> str:
    """Use LLM to synthesize investigation hops into a narrative."""
    evidence = []
    for hop in hops:
        if hop.get("success"):
            results = hop.get("results", [])
            snippets = [r.get("content", "")[:200] for r in results[:3] if r.get("content")]
            evidence.append(f"[{hop['hop']}] {hop['goal']}: {'; '.join(snippets)}")
        else:
            evidence.append(f"[{hop['hop']}] FAILED: {hop.get('error', 'unknown error')}")

    if cross_chain_matches and cross_chain_matches.get("resolved"):
        cc = cross_chain_matches
        evidence.append(f"[CROSS_CHAIN] Found {cc.get('total_matches', 0)} matches on other chains")

    context = "\n".join(evidence)

    prompt = f"""You are a crypto forensics analyst. Synthesize the following investigation
evidence into a clear, chronological narrative report. Use plain English. Include:

1. Summary: What happened? (2-3 sentences)
2. Timeline: Key events in order
3. Key entities: Addresses, tokens, exchanges involved
4. Fund flow: How the money moved
5. Risk assessment: How confident are these findings?
6. Recommendations: What should the user do?

Query: {query}

Evidence:
{context}

Write the report now:"""

    try:
        from app.llm_config import generate_content

        return await generate_content(
            prompt,
            system="You are a blockchain forensics investigator. Be precise, chronological, and evidence-based.",
            max_tokens=2048,
            temperature=0.4,
        )
    except Exception as exc:
        return f"Narrative generation error: {exc}\n\nRaw evidence:\n{context}"


async def investigate(
    query: str,
    investigation_type: str = "auto",
    max_hops: int = 5,
    collections: list[str] | None = None,
    cross_chain: bool = False,
    cross_chain_address: str = "",
    cross_chain_chain: str = "",
) -> dict[str, Any]:
    """
    Run a multi-hop forensic investigation.

    Parameters
    ----------
    query: The investigation query (address, token, or natural language)
    investigation_type: "rug_pull", "honeypot", "phishing", "general", or "auto"
    max_hops: Maximum investigation hops
    collections: RAG collections to search
    cross_chain: Enable cross-chain entity resolution
    cross_chain_address: Address for cross-chain resolution
    cross_chain_chain: Chain for cross-chain resolution

    Returns
    -------
    Dict with:
        - narrative: Full investigation report (LLM-synthesized)
        - hops: List of executed investigation hops
        - cross_chain: Cross-chain resolution results
        - confidence: Overall confidence score
        - evidence_count: Total evidence items gathered
    """
    start_time = time.time()

    if not collections:
        collections = DEFAULT_COLLECTIONS

    # Auto-detect investigation type
    if investigation_type == "auto":
        ql = query.lower()
        if any(w in ql for w in ["rug", "pull", "liquidity", "exit"]):
            investigation_type = "rug_pull"
        elif any(w in ql for w in ["honeypot", "cant sell", "transfer restriction"]):
            investigation_type = "honeypot"
        elif any(w in ql for w in ["phish", "drain", "fake", "impersonat"]):
            investigation_type = "phishing"
        else:
            investigation_type = "general"

    # Get investigation plan
    hops_plan = HOP_TEMPLATES.get(investigation_type, HOP_TEMPLATES["general"])[:max_hops]

    # Execute hops sequentially (each feeds context to the next)
    hops_executed = []
    working_query = query
    for hop in hops_plan:
        result = await _execute_hop(hop, working_query, collections)
        hops_executed.append(result)
        if result.get("success"):
            # Feed evidence into next hop's query
            top_content = result.get("results", [{}])[0].get("content", "")
            if top_content:
                working_query += f" (related: {top_content[:80]})"

    # Cross-chain resolution
    cc_result = None
    if cross_chain and cross_chain_address:
        try:
            from app.cross_chain import resolve_cross_chain_identity

            cc_result = resolve_cross_chain_identity(
                address=cross_chain_address,
                chain=cross_chain_chain or "ethereum",
                funding_sources=[
                    {
                        "address": r.get("id", ""),
                        "chain": r.get("metadata", {}).get("chain", "ethereum"),
                        "amount": 0,
                        "hop_distance": 1,
                    }
                    for hop in hops_executed
                    for r in hop.get("results", [])[:3]
                ],
            )
        except Exception as exc:
            logger.debug("cross_chain_resolution_failed: %s", exc)

    # Synthesize narrative
    narrative = await _synthesize_narrative(working_query, hops_executed, cc_result)

    # Aggregate confidence
    confidence_scores = [
        h.get("confidence", {}).get("score", 0)
        for h in hops_executed
        if h.get("success") and h.get("confidence")
    ]
    avg_confidence = (
        round(sum(confidence_scores) / len(confidence_scores)) if confidence_scores else 0
    )

    elapsed = time.time() - start_time

    return {
        "query": query,
        "investigation_type": investigation_type,
        "narrative": narrative,
        "hops": [
            {
                "hop": h["hop"],
                "goal": h.get("goal", ""),
                "success": h.get("success", False),
                "results_count": h.get("results_count", 0),
            }
            for h in hops_executed
        ],
        "cross_chain": cc_result,
        "confidence": {
            "score": avg_confidence,
            "label": "HIGH" if avg_confidence > 60 else "MEDIUM" if avg_confidence > 30 else "LOW",
        },
        "evidence_count": sum(h.get("results_count", 0) for h in hops_executed),
        "elapsed_seconds": round(elapsed, 1),
        "hops_executed": len(hops_executed),
        "summary": narrative.split("\n")[0] if narrative else "Investigation complete",
    }
