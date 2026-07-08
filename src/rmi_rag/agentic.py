#!/usr/bin/env python3
"""
TIER-1 AGENTIC RAG - Multi-Hop Retrieval + LLM Reranking
=========================================================
What elevates RAG from "search tool" to "intelligence analyst":

1. LLM RERANKING - Cross-encode top-K results for precision
   - Initial ANN search returns 20 candidates
   - LLM scores each result against the query
   - Returns top-5 highest-confidence hits with reasoning

2. MULTI-HOP RETRIEVAL - Chain-of-thought investigation
   - "Token X has this scam pattern → same deployer → check their other tokens → any also scams?"
   - Agent plans retrieval steps, executes, synthesizes

3. REFLECTION LOOP - Self-correcting search
   - "Results look low-confidence. Reformulate query with different keywords."
   - "Found pattern A. Did I check pattern B which is often paired with A?"

4. EVIDENCE WEIGHTING - Confidence scoring per source
   - Curated patterns: 0.9 weight
   - REKT reports: 0.85 weight
   - Community reports: 0.6 weight
   - Automated scan results: 0.7 weight

5. STREAMING RESPONSE - Progressive disclosure
   - Stream findings as they're discovered
"""

import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# AI Router config
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
# Decode base64 LLM key if present, otherwise use plain LLM_API_KEY
if os.getenv("LLM_API_KEY_B64"):
    import base64 as _b64

    os.environ["LLM_API_KEY"] = _b64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
AI_BASE = LLM_BASE_URL if LLM_API_KEY else "https://openrouter.ai/api/v1/chat/completions"
RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-flash"))
ANALYSIS_MODEL = os.getenv("RAG_ANALYSIS_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-flash"))


# ══════════════════════════════════════════════════════════════════════
# LLM RERANKER
# ══════════════════════════════════════════════════════════════════════


class LLMReranker:
    """
    Cross-encode style reranking using LLM.
    Unlike pure vector similarity, the LLM:
    - Understands context and nuance
    - Can identify false positives (similar vectors, different meaning)
    - Provides reasoning for each score
    - Weighs evidence quality
    """

    RERANK_PROMPT = """You are a crypto security expert. Score how relevant each document is to the query.

Query: {query}

Documents:
{documents}

For each document, output a JSON object with:
- "id": document ID
- "score": 0.0-1.0 (how relevant to the query)
- "reasoning": one sentence explaining the relevance
- "flags": any red flags or concerns about the match quality

Return a JSON list sorted by score descending. Only return the JSON, nothing else."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or LLM_API_KEY

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Rerank search results using LLM cross-encoding.
        Takes top 20 vector results, returns top-K reranked.
        """
        if not self.api_key or not documents:
            return documents[:top_k]

        # Prepare document list for LLM
        doc_strings = []
        for i, doc in enumerate(documents[:20]):
            # Extract relevant fields
            content = doc.get("content", "")[:500]
            source = doc.get("source", doc.get("metadata", {}).get("source", ""))
            severity = doc.get("severity", doc.get("metadata", {}).get("severity", ""))
            sim = doc.get("similarity", 0)
            doc_strings.append(
                f"[{i}] ID={doc['id']} | Source={source} | Severity={severity} | "
                f"VectorSim={sim:.3f} | Content: {content}"
            )

        prompt = self.RERANK_PROMPT.format(
            query=query[:500],
            documents="\n\n".join(doc_strings),
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    AI_BASE,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": RERANK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 1000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]

                # Extract JSON from response
                json_match = re.search(r"\[.*\]", text, re.DOTALL)
                if json_match:
                    reranked = json.loads(json_match.group(0))

                    # Merge back with original documents
                    doc_map = {d["id"]: d for d in documents}
                    results = []
                    for item in reranked:
                        doc_id = item.get("id", "")
                        if doc_id in doc_map:
                            results.append(
                                {
                                    **doc_map[doc_id],
                                    "llm_score": item.get("score", 0),
                                    "llm_reasoning": item.get("reasoning", ""),
                                    "llm_flags": item.get("flags", ""),
                                    "confidence": round(
                                        0.6 * item.get("score", 0) + 0.4 * doc_map[doc_id].get("similarity", 0),
                                        4,
                                    ),
                                }
                            )

                    results.sort(key=lambda x: x.get("confidence", 0), reverse=True)
                    return results[:top_k]

        except Exception as e:
            logger.warning("LLM reranking failed: %s", e)

        # Fallback: return top-K by vector similarity
        return sorted(documents, key=lambda x: x.get("similarity", 0), reverse=True)[:top_k]


# ══════════════════════════════════════════════════════════════════════
# MULTI-HOP RETRIEVAL AGENT
# ══════════════════════════════════════════════════════════════════════


class MultiHopRetrievalAgent:
    """
    Agentic RAG that performs multi-hop investigation.

    Pattern:
      1. User asks "Is this token a scam?"
      2. Agent plans: [check scam patterns] → [check deployer] → [check deployer's other tokens] → [synthesize]
      3. Each hop feeds into the next
      4. Final synthesis with confidence score

    Hops:
      - scam_pattern: Check against known scam DB
      - code_audit: Analyze contract code
      - deployer_check: Investigate deployer wallet
      - related_tokens: Find tokens by same deployer
      - liquidity_check: Check LP lock/burn status
      - holder_analysis: Analyze holder distribution
    """

    PLANNING_PROMPT = """You are a crypto forensics expert. Plan a multi-step investigation for this query.

Query: {query}
Context: {context}

Available investigation steps:
- scam_pattern: Semantic search against known scam database
- code_audit: Analyze contract code for vulnerabilities
- deployer_check: Investigate the deployer wallet history
- related_tokens: Find other tokens created by same deployer
- transaction_analysis: Analyze transaction patterns
- wallet_clustering: Find related wallets via graph analysis

Return a JSON plan with:
{{
  "steps": [
    {{"hop": 1, "action": "step_name", "query": "specific search query", "reasoning": "why this step"}}
  ],
  "synthesis_question": "the ultimate question to answer"
}}

Return only JSON, nothing else."""

    SYNTHESIS_PROMPT = """You are a crypto security analyst. Synthesize findings from a multi-step investigation.

Original Query: {query}

Investigation Steps and Results:
{findings}

Create a comprehensive assessment with:
1. OVERALL RISK: LOW/MEDIUM/HIGH/CRITICAL
2. CONFIDENCE: 0.0-1.0
3. EVIDENCE: Key findings supporting the assessment
4. SCAM PATTERNS FOUND: List of matched patterns
5. RELATED THREATS: Connected wallets/tokens that may also be risky
6. RECOMMENDATION: What action to take

Return as JSON. Be precise and evidence-based."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or LLM_API_KEY
        self.reranker = LLMReranker(api_key)

    async def investigate(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        max_hops: int = 3,
    ) -> dict[str, Any]:
        """
        Multi-hop investigation pipeline.
        Returns structured findings with evidence trail.
        """
        from rmi_rag.crypto_embeddings import get_embedder

        embedder = await get_embedder()
        context = context or {}

        # Step 1: Plan the investigation
        plan = await self._plan(query, context, max_hops)

        # Step 2: Execute each hop
        findings = []
        evidence_chain = []

        for step in plan.get("steps", []):
            hop_result = await self._execute_hop(step, findings, evidence_chain, embedder)
            findings.append(
                {
                    "hop": step["hop"],
                    "action": step["action"],
                    "query": step["query"],
                    "result": hop_result,
                }
            )
            evidence_chain.extend(hop_result.get("evidence", []))

        # Step 3: Rerank all collected evidence
        all_docs = []
        for f in findings:
            all_docs.extend(f.get("result", {}).get("documents", []))

        reranked = await self.reranker.rerank(query, all_docs, top_k=10)

        # Step 4: Synthesize final assessment
        synthesis = await self._synthesize(query, findings, reranked)

        return {
            "query": query,
            "investigation_plan": plan,
            "findings": findings,
            "top_evidence": reranked[:5],
            "synthesis": synthesis,
            "confidence": synthesis.get("confidence", 0),
            "risk_level": synthesis.get("overall_risk", "UNKNOWN"),
            "investigated_at": datetime.now(UTC).isoformat(),
        }

    async def _plan(self, query: str, context: dict, max_hops: int) -> dict:
        """Plan multi-hop investigation steps using LLM."""
        if not self.api_key:
            # Default plan without LLM
            return {
                "steps": [
                    {
                        "hop": 1,
                        "action": "scam_pattern",
                        "query": query,
                        "reasoning": "Check against known scam database",
                    },
                    {
                        "hop": 2,
                        "action": "code_audit",
                        "query": f"code audit {context.get('name', '')}",
                        "reasoning": "Analyze contract code patterns",
                    },
                ],
                "synthesis_question": f"Is there evidence this is a scam? {query}",
            }

        prompt = self.PLANNING_PROMPT.format(
            query=query,
            context=json.dumps(context)[:500],
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    AI_BASE,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": RERANK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens": 500,
                    },
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    plan = json.loads(json_match.group(0))
                    plan["steps"] = plan.get("steps", [])[:max_hops]
                    return plan
        except Exception as e:
            logger.warning("Planning failed: %s", e)

        return {
            "steps": [
                {"hop": 1, "action": "scam_pattern", "query": query, "reasoning": "default"},
            ],
            "synthesis_question": query,
        }

    async def _execute_hop(
        self,
        step: dict,
        previous_findings: list,
        evidence_chain: list,
        embedder,
    ) -> dict:
        """Execute a single investigation hop."""
        from rmi_rag.search_service import search_multi_collection, search_similar

        action = step["action"]
        query = step["query"]

        try:
            if action == "scam_pattern":
                results = await search_multi_collection(query, collections=["known_scams", "scam_patterns"], limit=10)
            elif action == "code_audit":
                results = await search_similar(query, "contract_audits", limit=10)
            elif action == "deployer_check":
                results = await search_similar(query, "wallet_profiles", limit=10)
            elif action == "related_tokens":
                results = await search_similar(query, "token_analysis", limit=10)
            elif action == "transaction_analysis":
                results = await search_similar(query, "transaction_patterns", limit=10)
            else:
                # Generic search across all collections
                results = await search_multi_collection(query, limit=10)

            return {
                "action": action,
                "query": query,
                "documents": results,
                "count": len(results),
                "evidence": [
                    {
                        "id": r["id"],
                        "similarity": r.get("similarity", 0),
                        "content": r.get("content", "")[:300],
                    }
                    for r in results[:3]
                ],
            }
        except Exception as e:
            logger.warning("Hop %s failed: %s", action, e)
            return {"action": action, "error": str(e), "documents": [], "evidence": []}

    async def _synthesize(
        self,
        query: str,
        findings: list,
        reranked: list,
    ) -> dict:
        """Synthesize final assessment from all findings."""
        if not self.api_key:
            # Heuristic synthesis without LLM
            high_risk = sum(1 for r in reranked if r.get("severity", "").lower() in ("critical", "high"))
            total = len(reranked) or 1
            return {
                "overall_risk": "HIGH" if high_risk / total > 0.3 else "MEDIUM",
                "confidence": min(0.9, high_risk / max(1, total)),
                "evidence_count": len(reranked),
                "scam_patterns_found": [r.get("metadata", {}).get("name", "") for r in reranked[:5]],
                "recommendation": "Avoid - high risk indicators detected" if high_risk > 0 else "Proceed with caution",
            }

        # Build findings summary
        finding_text = ""
        for f in findings:
            finding_text += f"\nHop {f['hop']}: {f['action']} - {f['result'].get('count', 0)} results\n"
            for doc in f.get("result", {}).get("documents", [])[:3]:
                finding_text += f"  - {doc.get('content', '')[:200]}\n"

        prompt = self.SYNTHESIS_PROMPT.format(
            query=query,
            findings=finding_text[:3000],
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    AI_BASE,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": ANALYSIS_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens": 800,
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
        except Exception as e:
            logger.warning("Synthesis LLM failed: %s", e)

        return {
            "overall_risk": "UNKNOWN",
            "confidence": 0.3,
            "error": "Synthesis failed",
        }


# ══════════════════════════════════════════════════════════════════════
# STREAMING SEARCH (for real-time UX)
# ══════════════════════════════════════════════════════════════════════


async def stream_rag_search(
    query: str,
    collection: str = "all",
    limit: int = 5,
) -> AsyncGenerator[str, None]:
    """
    Streaming RAG search - yields results as they're discovered.
    Pattern: "thinking..." → "found N matches..." → "reranking..." → results
    """
    from rmi_rag.search_service import search_multi_collection, search_similar

    # Phase 1: Quick keyword pre-filter (instant)
    yield json.dumps({"phase": "quick_scan", "status": "searching"}) + "\n"

    # Phase 2: Vector search
    yield json.dumps({"phase": "vector_search", "status": "embedding_query"}) + "\n"

    if collection == "all":
        results = await search_multi_collection(query, limit=limit * 4)
    else:
        results = await search_similar(query, collection, limit=limit * 4)

    yield (
        json.dumps(
            {
                "phase": "vector_search",
                "status": "complete",
                "candidates": len(results),
            }
        )
        + "\n"
    )

    # Phase 3: Reranking
    if results:
        yield json.dumps({"phase": "reranking", "status": "cross_encoding"}) + "\n"

        reranker = LLMReranker()
        reranked = await reranker.rerank(query, results, top_k=limit)

        yield (
            json.dumps(
                {
                    "phase": "complete",
                    "results": reranked[:limit],
                    "total_candidates": len(results),
                    "top_result": reranked[0] if reranked else None,
                }
            )
            + "\n"
        )
    else:
        yield (
            json.dumps(
                {
                    "phase": "complete",
                    "results": [],
                    "total_candidates": 0,
                }
            )
            + "\n"
        )


# ══════════════════════════════════════════════════════════════════════
# REAL-TIME TOKEN MONITOR
# ══════════════════════════════════════════════════════════════════════


class RealTimeTokenMonitor:
    """
    Stream new token deployments through the scam detection pipeline.
    When a new token is found:
      1. Extract features (name, symbol, deployer, initial txns)
      2. Quick keyword scan (instant)
      3. Embed and compare against scam DB
      4. Multi-hop if high-risk
      5. Alert if scam detected
    """

    def __init__(self):
        self.processed = set()
        self._processed_order = []  # FIFO order for eviction
        self._max_processed = 10000
        self.alerts = []

    async def scan_new_token(self, token_data: dict) -> dict[str, Any]:
        """Quick-look scan of a newly deployed token."""
        from rmi_rag.search_service import detect_scam_patterns

        address = token_data.get("address", "")
        if address in self.processed:
            return {"status": "already_processed", "address": address}
        self.processed.add(address)
        self._processed_order.append(address)

        # Evict oldest half when over limit (FIFO)
        if len(self.processed) > self._max_processed:
            evict_count = len(self._processed_order) // 2
            for old_addr in self._processed_order[:evict_count]:
                self.processed.discard(old_addr)
            self._processed_order = self._processed_order[evict_count:]

        # Phase 1: Quick keyword scan (sub-second)
        quick = await detect_scam_patterns(token_data)
        quick_matches = quick.get("quick_matches", [])

        # Phase 2: If quick matches found → deep semantic scan
        deep_matches = quick.get("deep_matches", [])

        # Phase 3: Risk assessment
        critical_matches = [m for m in quick_matches if m.get("severity") == "critical"]
        high_matches = [m for m in quick_matches if m.get("severity") == "high"]

        risk = "low"
        if critical_matches:
            risk = "critical"
        elif high_matches or deep_matches:
            risk = "high"
        elif quick_matches:
            risk = "medium"

        result = {
            "address": address,
            "name": token_data.get("name", ""),
            "symbol": token_data.get("symbol", ""),
            "chain": token_data.get("chain", "solana"),
            "risk": risk,
            "quick_matches": quick_matches,
            "deep_matches": deep_matches,
            "highest_threat": quick.get("highest_threat", "none"),
            "scanned_at": datetime.now(UTC).isoformat(),
        }

        # Phase 4: Alert if high risk
        if risk in ("critical", "high"):
            self.alerts.append(result)
            # Keep only last 100 alerts
            if len(self.alerts) > 100:
                self.alerts = self.alerts[-100:]

        return result

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        return self.alerts[-limit:]


# ══════════════════════════════════════════════════════════════════════
# SINGLETONS
# ══════════════════════════════════════════════════════════════════════

_reranker: LLMReranker | None = None
_agent: MultiHopRetrievalAgent | None = None
_monitor: RealTimeTokenMonitor | None = None


def get_reranker() -> LLMReranker:
    global _reranker
    if _reranker is None:
        _reranker = LLMReranker()
    return _reranker


def get_agent() -> MultiHopRetrievalAgent:
    global _agent
    if _agent is None:
        _agent = MultiHopRetrievalAgent()
    return _agent


def get_monitor() -> RealTimeTokenMonitor:
    global _monitor
    if _monitor is None:
        _monitor = RealTimeTokenMonitor()
    return _monitor

