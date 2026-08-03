"""
Graph RAG - Community detection + narrative summaries from Knowledge Graph.

Extends the existing Redis-backed Knowledge Graph (5,614 nodes) with:
  1. Label propagation community detection - find scammer rings, exchange clusters
  2. Community narrative synthesis - LLM-generated summaries per cluster
  3. Graph-augmented retrieval - expand queries with community context

Produces structured community reports that answer:
  "What scammer rings are active right now?"
  "Show me the cluster this address belongs to"
"""

import json
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


async def detect_communities(
    max_communities: int = 20,
    min_community_size: int = 3,
) -> list[dict[str, Any]]:
    """
    Detect communities in the Knowledge Graph using label propagation.

    Algorithm:
      1. Each node starts as its own community
      2. Iteratively: each node adopts the most common label among neighbors
      3. Converge after ~5 iterations
      4. Return top communities by size

    Returns list of community dicts with nodes, edges, and metadata.
    """
    try:
        from app.knowledge_graph import get_knowledge_graph  # external dependency

        kg = await get_knowledge_graph()
    except ImportError:
        logger.debug("Knowledge Graph not available")
        return []
    except Exception as e:
        logger.warning("KG access failed: %s", e)
        return []

    r = kg._redis_client if hasattr(kg, "_redis_client") else None
    if not r:
        logger.debug("No Redis client in KG")
        return []

    # 1. Get all nodes
    try:
        node_ids = list(await r.smembers("kg:nodes"))
        if not node_ids:
            logger.debug("KG has no nodes")
            return []
    except Exception as e:
        logger.warning("Failed to get KG nodes: %s", e)
        return []

    # 2. Get edges (adjacency)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for nid in node_ids:
        try:
            edges_raw = await r.smembers(f"kg:edges:{nid}")
            for edge in edges_raw:
                if isinstance(edge, bytes):
                    edge = edge.decode()
                parts = edge.split(":") if ":" in edge else [edge, ""]
                target = parts[1] if len(parts) > 1 else parts[0]
                if target in node_ids or target in {
                    b.decode() if isinstance(b, bytes) else b for b in node_ids
                }:
                    adjacency[nid].add(target)
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    # 3. Label propagation
    N_ITER = 5  # noqa: N806 - iteration constant
    labels = {nid: i for i, nid in enumerate(node_ids)}

    for _ in range(N_ITER):
        new_labels = {}
        for nid in node_ids:
            neighbors = adjacency.get(nid, set())
            if not neighbors:
                new_labels[nid] = labels.get(nid, 0)
                continue

            # Most common label among neighbors
            label_counts = defaultdict(int)
            for neighbor in neighbors:
                n_label = labels.get(neighbor)
                if n_label is not None:
                    label_counts[n_label] += 1

            if label_counts:
                new_labels[nid] = max(label_counts, key=label_counts.get)
            else:
                new_labels[nid] = labels.get(nid, 0)
        labels = new_labels

    # 4. Group into communities
    communities: dict[int, list[str]] = defaultdict(list)
    for nid, label in labels.items():
        communities[label].append(nid)

    # 5. Sort by size, filter minimum
    sorted_communities = sorted(
        communities.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    result = []
    for comm_id, (label, members) in enumerate(sorted_communities[:max_communities]):  # noqa: B007
        if len(members) < min_community_size:
            break

        # Get node types for community characterization
        node_types = defaultdict(int)
        for nid in members:
            try:
                node_data = await r.get(f"kg:node:{nid}")
                if node_data:
                    if isinstance(node_data, bytes):
                        node_data = node_data.decode()
                    data = json.loads(node_data) if node_data.startswith("{") else {}
                    ntype = data.get("type", "unknown")
                    node_types[ntype] += 1
            except Exception:
                node_types["unknown"] += 1

        # Get internal edges
        internal_edges = 0
        for nid in members:
            for neighbor in adjacency.get(nid, set()):
                if neighbor in members:
                    internal_edges += 1

        result.append(
            {
                "community_id": comm_id,
                "size": len(members),
                "members": members[:20],  # Top 20 members
                "node_types": dict(node_types),
                "internal_edges": internal_edges // 2,  # Undirected
                "density": round(
                    (internal_edges // 2) / max(len(members) * (len(members) - 1) / 2, 1), 4
                ),
                "dominant_type": max(node_types, key=node_types.get) if node_types else "unknown",
            }
        )

    return result


async def build_community_narrative(
    community: dict[str, Any],
    max_chars: int = 2000,
) -> str:
    """
    Generate a narrative summary of a detected community.

    Uses LLM to synthesize: what this cluster is, key addresses, risk profile.
    Falls back to heuristic summary if LLM unavailable.
    """
    community.get("members", [])
    node_types = community.get("node_types", {})
    dominant = community.get("dominant_type", "unknown")

    heuristic = (
        f"Community {community['community_id']}: {community['size']} nodes, "
        f"primarily {dominant} addresses. "
        f"Density: {community.get('density', 0):.3f}. "
        f"Member types: {json.dumps(dict(node_types))}"
    )

    # Try LLM synthesis
    try:
        from app.investigation_narratives import (  # external dependency
            AI_BASE,
            AI_MODEL,
            LLM_API_KEY,
        )

        if not LLM_API_KEY:
            return heuristic

        prompt = f"""Summarize this crypto wallet community:

Size: {community["size"]} addresses
Primary type: {dominant}
Density: {community.get("density", 0):.3f}
Member types: {json.dumps(dict(node_types))}

What kind of cluster is this? (scammer ring, exchange wallets, MEV bot network, etc.)
What is the risk profile? Limit to 3 sentences."""

        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                },
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    return heuristic


async def graph_rag_search(
    query: str,
    max_communities: int = 10,
) -> dict[str, Any]:
    """
    Full Graph RAG pipeline: detect communities → generate narratives → return.

    Returns communities with narrative summaries suitable for display.
    """
    communities = await detect_communities(max_communities=max_communities)

    # Generate narratives for top communities
    enriched = []
    for comm in communities[:5]:
        narrative = await build_community_narrative(comm)
        enriched.append({**comm, "narrative": narrative})

    return {
        "total_communities": len(communities),
        "communities": enriched,
        "total_nodes_in_communities": sum(c["size"] for c in communities),
        "summary": (
            f"Detected {len(communities)} communities across {sum(c['size'] for c in communities)} addresses."
        ),
    }


def synthesize_answer(
    answer: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Graph RAG ``synthesize_answer`` node — rating-aware answer synthesis.

    Uses Ground News-style ratings to improve answer quality:
      - **Verified sources are prioritized** in the citation list.
      - **Rumor-based claims carry a disclaimer** in the formatted answer.
      - **Corroboration counts are shown** in source badges and the
        returned ``rating_summary``.

    Delegates the heavy lifting to ``rmi_rag.ratings.synthesize_answer`` so
    retrieval, rerank and graph synthesis share one rating implementation.
    """
    from rmi_rag.ratings import synthesize_answer as _synthesize_rated

    return _synthesize_rated(answer, sources)
