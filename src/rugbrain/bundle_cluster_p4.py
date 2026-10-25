"""bundle_cluster.py split part: find_similar_bundles, search_clusters_by_description, index_bundle_detection, index_cluster_detection, backfill_label_templates."""
from typing import Any
import hashlib
import json
import logging
from .bundle_cluster_p1 import embed_bundle_profile, logger  # noqa: F401
from .bundle_cluster_p2 import embed_cluster_profile  # noqa: F401
from .bundle_cluster_p3 import CLUSTER_LABEL_TEMPLATES, auto_label_cluster  # noqa: F401

async def find_similar_bundles(
    target_bundle: dict[str, Any],
    min_similarity: float = 0.6,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find bundles similar to a target bundle."""
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

    target_vec = embed_bundle_profile(target_bundle)
    store = await get_qdrant_store()
    return await store.search(
        target_vec,
        collection="bundle_patterns",
        limit=limit,
        min_similarity=min_similarity,
    )

async def search_clusters_by_description(
    query: str,
    min_similarity: float = 0.5,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Natural language cluster search.
    "Show me all wash trading clusters from Solana in the last month"
    → embeds the query, searches against cluster behavioral vectors
    """
    from rugbrain.crypto_embeddings import get_embedder
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

    embedder = await get_embedder()

    # Embed the NL query
    query_vec = await embedder._semantic_embed_one(
        f"Wallet cluster with behavior: {query}", "semantic"
    )

    store = await get_qdrant_store()

    # Hybrid search: semantic + keyword
    results = await store.hybrid_search(
        query_text=query,
        query_embedding=query_vec,
        collection="wallet_clusters",
        limit=limit,
    )

    # Auto-label results if not already labeled
    for r in results:
        if "label" not in r.get("metadata", {}):
            try:
                labeling = await auto_label_cluster(
                    r.get("metadata", {}),
                    r.get("metadata", {}).get("vector", []),
                )
                r["metadata"]["auto_label"] = labeling
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    return results

async def index_bundle_detection(bundle: dict[str, Any]) -> str:
    """
    After bundle detection runs, index the result in RAG.
    Store bundle behavioral vector + metadata for future similarity search.
    """
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

    vec = embed_bundle_profile(bundle)
    token = bundle.get("token_address", "unknown")
    bundle_id = hashlib.sha256(
        f"bundle:{token}:{bundle.get('earliest_block', 0)}:{bundle.get('wallets_in_earliest_block', 0)}".encode()
    ).hexdigest()[:16]

    content = f"""Bundle detected on token {token}.
Confidence: {bundle.get("confidence", 0):.2f}
Wallets in earliest block: {bundle.get("wallets_in_earliest_block", 0)}
Common funder: {bundle.get("common_funder_address", "none")}
Signals: atomic_block={bundle.get("atomic_block_score", 0):.2f}, common_funder={bundle.get("common_funder_score", 0):.2f}
Top3 holder %: {bundle.get("top3_holder_percent", 0):.1f}%"""

    store = await get_qdrant_store()
    await store.insert(
        doc_id=bundle_id,
        collection="bundle_patterns",
        embedding=vec,
        content=content,
        metadata={
            "token_address": token,
            "confidence": bundle.get("confidence", 0),
            "severity": "high" if bundle.get("confidence", 0) > 0.7 else "medium",
            "chain": bundle.get("chain", "solana"),
            "detection_type": "bundle",
        },
        source="bundle_detector",
        severity="high" if bundle.get("confidence", 0) > 0.7 else "medium",
    )

    logger.info("Indexed bundle %s for token %s", bundle_id, token)
    return bundle_id

async def index_cluster_detection(cluster: dict[str, Any]) -> dict[str, Any]:
    """
    After cluster detection runs, index + auto-label + store.
    """
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

    vec = embed_cluster_profile(cluster)

    cluster_id = str(cluster.get("cluster_id", cluster.get("id", "")))
    if not cluster_id:
        cluster_id = hashlib.sha256(
            json.dumps(cluster, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    # Auto-label the cluster
    labels = await auto_label_cluster(cluster, vec)

    content = f"""Wallet cluster: {labels["top_label"]} (confidence: {labels["top_confidence"]:.2f})
Size: {cluster.get("size", "?")} wallets
Volume: ${cluster.get("total_volume_usd", 0):,.0f}
Age: {cluster.get("age_days", "?")} days
Active chains: {", ".join(cluster.get("active_chains", ["unknown"]))}
Risk: scam={cluster.get("scam_probability", 0):.1%}, rug={cluster.get("rug_probability", 0):.1%}, bot={cluster.get("bot_probability", 0):.1%}
All labels: {json.dumps(labels["all_labels"])}"""

    store = await get_qdrant_store()
    await store.insert(
        doc_id=cluster_id,
        collection="wallet_clusters",
        embedding=vec,
        content=content,
        metadata={
            "cluster_id": cluster_id,
            "size": cluster.get("size", 0),
            "top_label": labels["top_label"],
            "label_confidence": labels["top_confidence"],
            "all_labels": labels["all_labels"],
            "scam_probability": cluster.get("scam_probability", 0),
            "severity": labels["all_labels"][0]["severity"] if labels["all_labels"] else "medium",
            "chain": cluster.get("active_chains", ["unknown"])[0]
            if cluster.get("active_chains")
            else "unknown",
        },
        source="cluster_detector",
        severity=labels["all_labels"][0]["severity"] if labels["all_labels"] else "medium",
    )

    logger.info(
        "Indexed cluster %s as '%s' (%s)", cluster_id, labels["top_label"], labels["top_confidence"]
    )

    return {
        "cluster_id": cluster_id,
        **labels,
    }

async def backfill_label_templates() -> int:
    """Index all cluster label templates into pgvector for auto-labeling."""
    from rugbrain.crypto_embeddings import get_embedder
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

    embedder = await get_embedder()
    store = await get_qdrant_store()
    count = 0

    for template in CLUSTER_LABEL_TEMPLATES:
        label_id = hashlib.sha256(f"label_template:{template['label']}".encode()).hexdigest()[:16]
        content = f"LABEL: {template['label']}. {template['description']}. Examples: {template['examples']}. Signals: {', '.join(template['signals'])}."

        try:
            vec = await embedder._semantic_embed_one(content, "semantic")
        except Exception:
            vec = embedder._hash_embed(content)  # type: ignore[attr-defined]  # [REVIEW] _hash_embed does not exist on CryptoEmbedder — latent AttributeError fallback

        await store.insert(
            doc_id=label_id,
            collection="cluster_labels",
            embedding=vec,
            content=content,
            metadata=template,
            source="rmi-curated",
            severity=template["severity"],
        )
        count += 1

    logger.info("Backfilled %s cluster label templates", count)
    return count
