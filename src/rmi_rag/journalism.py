"""
Journalism Research Module — deep-dive research for crypto journalism.

Provides structured research briefs synthesising data from all three RAG
collections (``threat_intel``, ``rmi_agent_memory``, ``rmi_community``) to
support investigative journalism, bot content generation, and historical
analysis.

Typical usage::

    from rmi_rag.journalism import research_topic

    brief = await research_topic("cross-chain bridge hacks 2024")
    # brief contains synthesised timeline, entities, related scams, etc.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Collections relevant to journalism research
_COLLECTIONS = ["threat_intel", "rmi_agent_memory", "rmi_community"]
_MAX_DOCS_PER_COLLECTION = 15


async def research_topic(
    topic: str,
    max_docs: int = _MAX_DOCS_PER_COLLECTION,
    min_similarity: float = 0.45,
) -> dict[str, Any]:
    """Perform deep research on a given topic across all RAG collections.

    Queries all three pillar collections and synthesises a structured
    research brief containing:

    - **timeline**: Chronological events extracted from threat intel
    - **key_entities**: Named actors, groups, and patterns from agent memory
    - **related_scams**: Matching scam/hack records with severity
    - **community_insights**: Best practices and context from community knowledge
    - **synthesis**: Combined summary with cross-collection connections

    Args:
        topic:         The research topic (e.g. "Solana bridge security").
        max_docs:      Max documents to pull per collection.
        min_similarity: Cosine-similarity threshold (0.0-1.0).

    Returns:
        A dict with the structured research brief.

    Raises:
        ValueError: If topic is empty.
    """
    if not topic or not topic.strip():
        raise ValueError("topic is required for research")

    from rmi_rag.embeddings import get_embedding
    from rmi_rag.qdrant_store import QdrantVectorStore

    # Generate embedding for the topic query
    try:
        embedding = await get_embedding(topic)
    except Exception as e:
        logger.warning("research_embedding_failed topic=%s err=%s", topic[:60], e)
        return _empty_brief(topic, error=str(e)[:200])

    store = QdrantVectorStore()

    # Phase 1: Query each collection in parallel
    threat_results: list[dict[str, Any]] = []
    entity_results: list[dict[str, Any]] = []
    community_results: list[dict[str, Any]] = []

    import asyncio

    async def _search_coll(collection: str) -> list[dict[str, Any]]:
        try:
            return await store.search(
                query_embedding=embedding,
                collection=collection,
                limit=max_docs,
                min_similarity=min_similarity,
            )
        except Exception as e:
            logger.debug("research_search_failed coll=%s err=%s", collection, e)
            return []

    results = await asyncio.gather(
        _search_coll("threat_intel"),
        _search_coll("rmi_agent_memory"),
        _search_coll("rmi_community"),
    )
    threat_results, entity_results, community_results = results

    # Phase 2: Extract timeline from threat intel (hacks with year context)
    timeline = _extract_timeline(threat_results)

    # Phase 3: Extract key entities
    key_entities = _extract_entities(entity_results)

    # Phase 4: Classify related scams
    related_scams = _classify_related_scams(threat_results, topic)

    # Phase 5: Extract community insights
    community_insights = _extract_insights(community_results)

    # Phase 6: Build cross-collection connections
    connections = _build_connections(threat_results, entity_results, community_results)

    # Phase 7: Synthesise into brief
    brief = {
        "topic": topic,
        "research_timestamp": datetime.now(UTC).isoformat(),
        "collections_queried": _COLLECTIONS,
        "total_docs_retrieved": {
            "threat_intel": len(threat_results),
            "rmi_agent_memory": len(entity_results),
            "rmi_community": len(community_results),
        },
        "timeline": timeline,
        "key_entities": key_entities,
        "related_scams": related_scams,
        "community_insights": community_insights,
        "cross_collection_connections": connections,
        "top_results": _top_n(threat_results + entity_results + community_results, n=10),
        "synthesis": _synthesise(
            topic=topic,
            timeline=timeline,
            entities=key_entities,
            scams=related_scams,
            insights=community_insights,
            connections=connections,
        ),
    }

    return brief


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════


def _empty_brief(topic: str, error: str = "") -> dict[str, Any]:
    """Return a minimal research brief when no results found or an error."""
    return {
        "topic": topic,
        "research_timestamp": datetime.now(UTC).isoformat(),
        "status": "empty" if not error else "error",
        "error": error if error else None,
        "total_docs_retrieved": {"threat_intel": 0, "rmi_agent_memory": 0, "rmi_community": 0},
        "timeline": [],
        "key_entities": [],
        "related_scams": [],
        "community_insights": [],
        "cross_collection_connections": [],
        "top_results": [],
        "synthesis": _fallback_synthesis(topic),
    }


def _extract_timeline(results: list[dict]) -> list[dict[str, Any]]:
    """Extract chronological events from threat intel results.

    Looks for hack names, years, and amounts in the document content
    to build a structured timeline.
    """
    import re

    timeline = []
    for r in results:
        content = r.get("content", r.get("payload", {}).get("content", ""))
        if not content:
            continue

        # Try to extract year from content
        year_match = re.search(r"\b(20\d{2})\b", content)
        year = int(year_match.group(1)) if year_match else None

        # Try to extract amount
        amount_match = re.search(r"\$(\d+(?:,\d{3})*(?:\.\d+)?)", content)
        amount = amount_match.group(0) if amount_match else None

        # Try to extract a title/event name from the first line
        first_line = content.split("\n")[0] if content else ""
        event_name = first_line.replace("SCAM PATTERN:", "").replace("HISTORIC HACK:", "").strip()

        severity = r.get("severity", r.get("payload", {}).get("severity", "medium"))

        timeline.append(
            {
                "event": event_name or content[:80],
                "year": year,
                "amount": amount,
                "severity": severity,
                "similarity": round(r.get("similarity", 0), 4),
            }
        )

    # Sort by year descending (most recent first)
    timeline.sort(key=lambda x: x["year"] if x["year"] else 0, reverse=True)
    return timeline


def _extract_entities(results: list[dict]) -> list[dict[str, Any]]:
    """Extract named entities (threat actors, protocols, patterns) from agent memory."""
    entities = []
    for r in results:
        content = r.get("content", r.get("payload", {}).get("content", ""))
        severity = r.get("severity", r.get("payload", {}).get("severity", "medium"))
        source = r.get("source", r.get("payload", {}).get("source", ""))

        if not content:
            continue

        # Entity type classification
        entity_type = "actor"
        if "PATTERN" in content or "pattern" in content:
            entity_type = "vulnerability_pattern"
        elif "HACK" in content or "EXPLOIT" in content:
            entity_type = "exploit"
        elif "ENTITY" in content:
            entity_type = "threat_actor"

        # Extract entity name from first line
        first_line = content.split("\n")[0] if content else ""
        name = (
            first_line.replace("ENTITY PROFILE:", "")
            .replace("KNOWN ADDRESS:", "")
            .replace("SCAM PATTERN:", "")
            .replace("HISTORIC HACK:", "")
            .replace("COMMUNITY TOPIC:", "")
            .strip()
        )

        entities.append(
            {
                "name": name or content[:60],
                "type": entity_type,
                "severity": severity,
                "source": source,
                "relevance": round(r.get("similarity", 0), 4),
            }
        )

    return entities


def _classify_related_scams(results: list[dict], topic: str) -> list[dict[str, Any]]:
    """Filter and classify results that are specifically scam/hack related."""
    scams = []
    seen = set()

    for r in results:
        content = r.get("content", r.get("payload", {}).get("content", ""))
        if not content:
            continue

        # Deduplicate by content prefix
        key = content[:60]
        if key in seen:
            continue
        seen.add(key)

        severity = r.get("severity", r.get("payload", {}).get("severity", "medium"))

        # Check if this is scam-related content
        scam_keywords = [
            "scam",
            "hack",
            "exploit",
            "theft",
            "drain",
            "rug",
            "fraud",
            "phish",
            "malicious",
        ]
        if any(kw in content.lower() for kw in scam_keywords):
            scams.append(
                {
                    "summary": (content[:200] + "...") if len(content) > 200 else content,
                    "severity": severity,
                    "similarity": round(r.get("similarity", 0), 4),
                }
            )

    # Sort by severity: critical > high > medium > low
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    scams.sort(key=lambda x: severity_order.get(x["severity"], 99))
    return scams


def _extract_insights(results: list[dict]) -> list[dict[str, Any]]:
    """Extract community security insights and best practices."""
    insights = []
    for r in results:
        content = r.get("content", r.get("payload", {}).get("content", ""))
        if not content:
            continue

        first_line = content.split("\n")[0] if content else ""

        # Extract the insight/detail portion
        detail_lines = []
        capture = False
        for line in content.split("\n"):
            if line.startswith("Insight:") or line.startswith("insight:"):
                capture = True
                continue
            if capture and line.strip():
                detail_lines.append(line.strip())

        insight_text = " ".join(detail_lines) if detail_lines else ""

        insights.append(
            {
                "topic": first_line.replace("COMMUNITY TOPIC:", "").strip(),
                "insight": insight_text or content[:200],
                "relevance": round(r.get("similarity", 0), 4),
            }
        )

    return insights


def _build_connections(
    threat_results: list[dict],
    entity_results: list[dict],
    community_results: list[dict],
) -> list[dict[str, Any]]:
    """Find cross-collection connections — entities mentioned in both
    threat intel and agent memory, or community guidance related to
    detected threats."""
    # Extract entity names from agent memory
    entity_names = set()
    for r in entity_results:
        content = r.get("content", r.get("payload", {}).get("content", "")) or ""
        for line in content.split("\n"):
            if (
                ":" in line
                and not line.startswith("ENTITY PROFILE")
                and not line.startswith("KNOWN ADDRESS")
            ):
                entity_names.add(line.split(":")[0].strip())

    connections = []
    for r in threat_results:
        content = r.get("content", r.get("payload", {}).get("content", "")) or ""
        matched = [en for en in entity_names if en.lower() in content.lower()]
        if matched:
            connections.append(
                {
                    "type": "entity_in_threat_context",
                    "entities": matched,
                    "context": content[:120],
                }
            )

    return connections


def _top_n(results: list[dict], n: int = 10) -> list[dict[str, Any]]:
    """Return top-N results by similarity, with trimmed content."""
    sorted_results = sorted(results, key=lambda x: x.get("similarity", 0), reverse=True)[:n]

    top = []
    for r in sorted_results:
        content = r.get("content", r.get("payload", {}).get("content", ""))
        top.append(
            {
                "id": r.get("id", ""),
                "similarity": round(r.get("similarity", 0), 4),
                "severity": r.get("severity", r.get("payload", {}).get("severity", "")),
                "source": r.get("source", r.get("payload", {}).get("source", "")),
                "summary": (content[:250] + "...")
                if content and len(content) > 250
                else (content or ""),
            }
        )

    return top


def _synthesise(
    topic: str,
    timeline: list[dict],
    entities: list[dict],
    scams: list[dict],
    insights: list[dict],
    connections: list[dict],
) -> dict[str, Any]:
    """Build a text synthesis from all research components.

    This generates a structured summary without relying on an external LLM,
    making it fast and always-available for bot/journalism use cases.
    """
    parts: list[str] = []

    # Header
    parts.append(f"## Research Brief: {topic}")

    # Summary stats
    total_events = len(timeline)
    total_scams = len(scams)
    total_entities = len(entities)
    parts.append(
        f"\n**Summary:** Found {total_events} timeline events, {total_scams} related "
        f"scam/hack records, and {total_entities} key entities across all intelligence collections."
    )

    # Timeline
    if timeline:
        parts.append(f"\n### Timeline ({len(timeline)} events)")
        parts.append("| Event | Year | Severity | Amount |")
        parts.append("|-------|------|----------|--------|")
        for ev in timeline[:8]:
            parts.append(
                f"| {ev['event'][:60]} | {ev['year'] or 'N/A'} | {ev['severity']} | {ev['amount'] or 'N/A'} |"
            )

    # Key entities
    if entities:
        parts.append(f"\n### Key Entities ({len(entities)})")
        for ent in entities[:6]:
            parts.append(f"- **{ent['name']}** ({ent['type']}) — severity: {ent['severity']}")

    # Related scams
    if scams:
        critical_count = sum(1 for s in scams if s["severity"] == "critical")
        high_count = sum(1 for s in scams if s["severity"] == "high")
        parts.append("\n### Related Scams & Exploits")
        parts.append(
            f"Found {len(scams)} related records ({critical_count} critical, {high_count} high)."
        )
        top_scams = [s for s in scams if s["severity"] in ("critical", "high")][:4]
        for s in top_scams:
            parts.append(f"- [{s['severity'].upper()}] {s['summary'][:120]}")

    # Cross-collection connections
    if connections:
        parts.append(f"\n### Cross-Collection Connections ({len(connections)})")
        for conn in connections[:4]:
            entities_str = ", ".join(conn["entities"])
            parts.append(f"- **{entities_str}** appears in threat context: {conn['context'][:100]}")

    # Community guidance
    if insights:
        parts.append("\n### Community Guidance")
        for ins in insights[:3]:
            topic_name = ins.get("topic", "")
            insight_text = ins.get("insight", "")
            if topic_name:
                parts.append(f"- **{topic_name}**: {insight_text[:150]}")

    # Key metrics
    parts.append("\n### Research Quality Metrics")
    parts.append("- Collections queried: 3 (threat_intel, rmi_agent_memory, rmi_community)")
    parts.append(f"- Timeline coverage: {len(timeline)} events")
    parts.append(f"- Entity identification: {len(entities)} entities profiled")
    parts.append(f"- Scam/hack records evaluated: {len(scams)}")
    parts.append(f"- Cross-references discovered: {len(connections)}")

    return {
        "markdown": "\n".join(parts),
        "summary_stats": {
            "total_events": total_events,
            "total_scams": total_scams,
            "total_entities": total_entities,
            "cross_connections": len(connections),
            "critical_findings": sum(1 for s in scams if s["severity"] == "critical")
            if scams
            else 0,
        },
    }


def _fallback_synthesis(topic: str) -> dict[str, Any]:
    """Return a minimal synthesis when no results found."""
    return _synthesise(
        topic=topic,
        timeline=[],
        entities=[],
        scams=[],
        insights=[],
        connections=[],
    )
