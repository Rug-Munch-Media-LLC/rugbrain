#!/usr/bin/env python3
"""
BUNDLE & CLUSTER RAG INTEGRATION
=================================
Marries graph-based detection with semantic intelligence.

What RAG adds to bundle/cluster detection:
  1. BEHAVIORAL EMBEDDING - Convert cluster behavior to vectors, store in pgvector
  2. SEMANTIC LABELING - Auto-label clusters ("insider ring", "MEV bot farm", "sybil attack")
  3. SIMILARITY SEARCH - "Find clusters that look like this known scammer group"
  4. CROSS-CHAIN IDENTITY - Match behavioral fingerprints across chains
  5. EVIDENCE CHAIN - Link clusters to known scam patterns, forensic reports
  6. NL QUERYING - "Show me all wash trading clusters from the last week"

Flow:
  BundleDetector → finds bundles → embed bundle profile → store in RAG
  ClusterDetector → finds clusters → embed cluster behavior → semantic label → store
  User queries → embed query → ANN search → return labeled clusters with evidence
"""

import hashlib
import json
import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# BUNDLE BEHAVIORAL EMBEDDER
# ══════════════════════════════════════════════════════════════════════


def embed_bundle_profile(bundle: dict[str, Any]) -> list[float]:
    """
    Convert a bundle detection result into a behavioral vector.
    Captures: timing patterns, wallet distribution, funding structure, concentration.

    Returns 128-dim vector that can be compared across bundles.
    """
    vec = np.zeros(128, dtype=np.float32)

    # ── Timing signals (dims 0-15) ──
    vec[0] = min(float(bundle.get("confidence", 0)), 1.0)
    vec[1] = min(float(bundle.get("atomic_block_score", 0)), 1.0)
    vec[2] = min(float(bundle.get("common_funder_score", 0)), 1.0)
    vec[3] = min(float(bundle.get("temporal_score", 0)), 1.0)
    vec[4] = min(float(bundle.get("distribution_anomaly_score", 0)), 1.0)
    vec[5] = min(float(bundle.get("concentration_score", 0)), 1.0)

    # ── Scale signals (dims 6-15) ──
    wallets = bundle.get("wallets_in_earliest_block", 0)
    vec[6] = min(float(wallets) / 100.0, 1.0)
    vec[7] = min(float(bundle.get("total_bundle_wallets", wallets)) / 100.0, 1.0)

    # Funding structure
    funders = bundle.get("unique_funders", 0)
    vec[8] = 1.0 / max(1.0, float(funders))  # fewer funders = more suspicious
    vec[9] = 1.0 if bundle.get("common_funder_address") else 0.0

    # Distribution
    top3_pct = float(bundle.get("top3_holder_percent", 0))
    vec[10] = min(top3_pct / 100.0, 1.0)
    top10_pct = float(bundle.get("top10_holder_percent", 0))
    vec[11] = min(top10_pct / 100.0, 1.0)

    # Temporal
    block_span = float(bundle.get("block_span", 1))
    vec[12] = min(1.0 / max(1.0, block_span), 1.0)  # narrower span = more bundled
    vec[13] = 1.0 if bundle.get("earliest_block", 0) == bundle.get("launch_block", 0) else 0.0  # block-0 bundle

    # ── Behavior fingerprint (dims 16-31) ──
    behaviors = bundle.get("behaviors", [])
    behavior_tags = [
        "coordinated_buy",
        "staggered_entry",
        "same_amount",
        "round_numbers",
        "gas_price_clustering",
        "mev_used",
        "jito_tip_paid",
        "flashbots_used",
        "same_dex_route",
        "same_slippage",
        "reverted_txns",
        "sandwich_pattern",
        "pump_then_dump",
        "slow_accumulation",
        "wash_trade",
        "sybil_pattern",
    ]
    for i, tag in enumerate(behavior_tags):
        if tag in behaviors:
            vec[16 + i] = 1.0

    # ── Entity hash of key addresses (dims 32-47) ──
    key_addrs = str(bundle.get("common_funder_address", ""))
    key_addrs += str(bundle.get("token_address", ""))
    key_addrs += ",".join(sorted(bundle.get("bundle_wallets", [])[:10]))
    addr_hash = hashlib.sha256(key_addrs.encode()).digest()
    for i in range(16):
        vec[32 + i] = addr_hash[i] / 255.0

    # ── Chain/context (dims 48-63) ──
    chain = bundle.get("chain", "solana").lower()
    chain_list = [
        "solana",
        "ethereum",
        "base",
        "bsc",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "fantom",
        "tron",
        "sui",
        "aptos",
        "near",
        "injective",
        "sei",
        "blast",
    ]
    for i, ch in enumerate(chain_list):
        if ch in chain:
            vec[48 + i] = 1.0

    # ── Risk classification hash (dims 64-79) ──
    risk_tags = bundle.get("risk_tags", [])
    risk_names = [
        "scam",
        "rug",
        "honeypot",
        "wash_trade",
        "insider",
        "bot_farm",
        "sybil",
        "sandwich_bot",
        "mev_bot",
        "market_maker",
        "whale",
        "exchange",
        "vault",
        "bridge",
        "mixer",
        "unknown",
    ]
    for i, tag in enumerate(risk_names):
        if tag in risk_tags or tag in str(bundle.get("classification", "")):
            vec[64 + i] = 1.0

    # ── Numerical fingerprint (dims 80-127) ──
    # Encode key metrics as normalized values (dims 80-85)
    metrics = [
        ("avg_buy_amount", 10000),
        ("max_buy_amount", 100000),
        ("avg_hold_time_blocks", 1000),
        ("sell_ratio", 1.0),
        ("profit_ratio", 10.0),
        ("gas_spent_eth", 1.0),
    ]
    for i, (key, scale) in enumerate(metrics):
        val = float(bundle.get(key, 0) or 0)
        vec[80 + i] = min(val / max(1, scale), 1.0)

    # Structural hash of the full bundle data (dims 86-127)
    full_hash = hashlib.sha256(json.dumps(bundle, sort_keys=True, default=str).encode()).digest()
    for i in range(42):
        vec[86 + i] = full_hash[i % 32] / 255.0

    return vec.tolist()


# ══════════════════════════════════════════════════════════════════════
# CLUSTER BEHAVIORAL EMBEDDER
# ══════════════════════════════════════════════════════════════════════


def embed_cluster_profile(cluster: dict[str, Any]) -> list[float]:
    """
    Convert a wallet cluster into a 192-dim behavioral vector.
    Captures: size, density, activity patterns, token overlap, temporal cohesion.

    This allows: "find clusters similar to this rug pull ring"
    """
    vec = np.zeros(192, dtype=np.float32)

    # ── Size & density (dims 0-19) ──
    size = int(cluster.get("size", cluster.get("wallet_count", 1)))
    vec[0] = min(np.log1p(size) / 10.0, 1.0)
    vec[1] = min(float(size) / 1000.0, 1.0)

    density = float(cluster.get("density", cluster.get("edge_density", 0)))
    vec[2] = min(density, 1.0)

    vec[3] = min(float(cluster.get("avg_degree", 0)) / 100.0, 1.0)
    vec[4] = min(float(cluster.get("diameter", 1)) / 10.0, 1.0)

    # ── Activity patterns (dims 10-29) ──
    age_days = float(cluster.get("age_days", 1))
    vec[10] = min(age_days / 365.0, 1.0)

    txn_count = float(cluster.get("total_transactions", 0))
    vec[11] = min(np.log1p(txn_count) / 15.0, 1.0)

    volume = float(cluster.get("total_volume_usd", 0))
    vec[12] = min(np.log1p(volume) / 20.0, 1.0)

    vec[13] = min(float(cluster.get("txn_frequency_per_day", 0)) / 100.0, 1.0)

    # Burstiness
    vec[14] = min(float(cluster.get("burst_score", 0)), 1.0)
    vec[15] = min(float(cluster.get("peak_activity_ratio", 0)), 1.0)

    # Sleep/active patterns
    vec[16] = 1.0 if cluster.get("sleeper_cluster") else 0.0
    vec[17] = min(float(cluster.get("dormant_period_days", 0)) / 365.0, 1.0)

    # ── Token overlap (dims 20-39) ──
    tokens = cluster.get("common_tokens", [])
    vec[20] = min(len(tokens) / 500.0, 1.0)
    vec[21] = min(float(cluster.get("token_overlap_ratio", 0)), 1.0)

    token_categories = cluster.get("token_categories", [])
    cat_tags = [
        "memecoin",
        "defi",
        "nft",
        "gaming",
        "stablecoin",
        "wrapped",
        "bridge",
        "oracle",
        "governance",
        "mev",
    ]
    for i, cat in enumerate(cat_tags):
        if cat in token_categories:
            vec[22 + i] = 1.0

    # ── Behavior classification (dims 30-49) ──
    signals = cluster.get("signals", cluster.get("behavior_signals", []))
    signal_tags = [
        "coordinated_trading",
        "pump_and_dump",
        "wash_trading",
        "insider_trading",
        "front_running",
        "sandwich_attacks",
        "arbitrage",
        "liquidation_cascade",
        "flash_loan_pattern",
        "mixer_usage",
        "tornado_cash",
        "cex_deposit_pattern",
        "dex_only",
        "nft_wash",
        "airdrop_farming",
        "sybil_attack",
        "bot_activity",
        "mev_extraction",
        "cross_chain_bridge",
        "stablecoin_only",
    ]
    for i, tag in enumerate(signal_tags):
        if tag in signals or tag in str(cluster.get("classification", "")):
            vec[30 + i] = 1.0

    # ── Cross-chain signals (dims 50-59) ──
    chains = cluster.get("active_chains", [])
    chain_list = [
        "ethereum",
        "solana",
        "base",
        "bsc",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "fantom",
        "tron",
    ]
    for i, ch in enumerate(chain_list):
        if ch in [c.lower() for c in chains]:
            vec[50 + i] = 1.0

    # ── Entity fingerprint (dims 60-79) ──
    entity_id = str(cluster.get("entity_id", ""))
    if entity_id:
        eh = hashlib.sha256(entity_id.encode()).digest()
        for i in range(16):
            vec[60 + i] = eh[i] / 255.0

    # ── Risk scoring (dims 80-89) ──
    vec[80] = min(float(cluster.get("scam_probability", 0)), 1.0)
    vec[81] = min(float(cluster.get("rug_probability", 0)), 1.0)
    vec[82] = min(float(cluster.get("honeypot_probability", 0)), 1.0)
    vec[83] = min(float(cluster.get("wash_trade_probability", 0)), 1.0)
    vec[84] = min(float(cluster.get("insider_probability", 0)), 1.0)
    vec[85] = min(float(cluster.get("bot_probability", 0)), 1.0)

    # ── Hash fingerprint (dims 90-191) ──
    ch = hashlib.sha256(json.dumps(cluster, sort_keys=True, default=str).encode()).digest()
    for i in range(102):
        vec[90 + i] = ch[i % 32] / 255.0

    return vec.tolist()


# ══════════════════════════════════════════════════════════════════════
# SEMANTIC LABELING
# ══════════════════════════════════════════════════════════════════════

CLUSTER_LABEL_TEMPLATES = [
    {
        "label": "insider_trading_ring",
        "description": "Cluster of wallets consistently buying before major announcements or listings, then selling into the pump.",
        "signals": ["coordinated_trading", "pump_and_dump", "insider_trading", "pre_listing_buys"],
        "severity": "high",
        "examples": "TRB insider ring, Binance listing front-runners",
    },
    {
        "label": "wash_trading_farm",
        "description": "Group of wallets trading the same tokens back and forth to simulate volume and attract real traders.",
        "signals": [
            "wash_trading",
            "circular_transfers",
            "same_amount_trades",
            "no_net_position_change",
        ],
        "severity": "high",
        "examples": "NFT wash trading rings, DEX volume inflation farms",
    },
    {
        "label": "sybil_attack_farm",
        "description": "Thousands of wallets controlled by one entity to manipulate voting, airdrops, or metrics.",
        "signals": [
            "sybil_attack",
            "airdrop_farming",
            "one_to_many_funding",
            "no_organic_activity",
        ],
        "severity": "high",
        "examples": "Hop Protocol sybils, Arbitrum airdrop farmers",
    },
    {
        "label": "mev_bot_network",
        "description": "Coordinated MEV bots running sandwich attacks, arbitrage, and liquidations.",
        "signals": [
            "mev_extraction",
            "sandwich_attacks",
            "arbitrage",
            "bot_activity",
            "flashbots_used",
        ],
        "severity": "medium",
        "examples": "jaredfromsubway.eth network, Banana Gun bot wallets",
    },
    {
        "label": "bundle_launch_ring",
        "description": "Creator uses 10-50 wallets to buy at launch (block 0), then dumps on retail.",
        "signals": ["coordinated_buy", "block_zero_bundle", "same_funder", "distributed_dump"],
        "severity": "critical",
        "examples": "Pump.fun bundle launches, sniper bot farms",
    },
    {
        "label": "liquidity_drain_cartel",
        "description": "Multiple wallets that sequentially drain liquidity from tokens after hype phase.",
        "signals": ["liquidity_removal", "multi_token_pattern", "sequential_rug", "same_deployer"],
        "severity": "critical",
        "examples": "Compounder finance drainers, sequential rug rings",
    },
    {
        "label": "market_maker_cluster",
        "description": "Legitimate market making operation - multiple wallets providing liquidity across DEXes.",
        "signals": ["market_maker", "arbitrage", "dex_only", "high_volume", "low_profit_margin"],
        "severity": "low",
        "examples": "Wintermute, Jump Trading, GSR wallet clusters",
    },
    {
        "label": "exchange_hot_wallet_ring",
        "description": "Cluster of wallets belonging to a centralized exchange's hot wallet system.",
        "signals": ["cex_deposit_pattern", "high_volume", "many_counterparties", "exchange"],
        "severity": "low",
        "examples": "Binance hot wallets, Coinbase deposit addresses",
    },
    {
        "label": "bridge_exploiter_ring",
        "description": "Wallets involved in cross-chain bridge exploits, often funded by the same mixer.",
        "signals": ["cross_chain_bridge", "mixer_usage", "tornado_cash", "one_time_use"],
        "severity": "critical",
        "examples": "Wormhole exploiter, Ronin bridge attacker wallets",
    },
    {
        "label": "nft_insider_mint_ring",
        "description": "Group minting rare NFTs before public reveal using insider knowledge of rarity.",
        "signals": ["nft_wash", "insider_trading", "pre_reveal_mints", "rarity_sniping"],
        "severity": "high",
        "examples": "OpenSea insider trading, Blur farmer rings",
    },
]


async def auto_label_cluster(
    cluster: dict[str, Any],
    cluster_vector: list[float],
) -> dict[str, Any]:
    """
    Auto-label a cluster by comparing its behavioral vector to known templates.
    Uses cosine similarity between cluster behavior and template descriptions.
    """
    from rmi_rag.crypto_embeddings import get_embedder

    embedder = await get_embedder()
    signals = cluster.get("signals", cluster.get("behavior_signals", []))

    # Build semantic description of the cluster
    cluster_desc = f"""Wallet cluster with {cluster.get("size", "?")} wallets.
Age: {cluster.get("age_days", "?")} days.
Volume: ${cluster.get("total_volume_usd", 0):,.0f}.
Transactions: {cluster.get("total_transactions", 0)}.
Signals: {", ".join(signals[:10])}.
Active chains: {", ".join(cluster.get("active_chains", ["unknown"]))}.
Common tokens: {", ".join(cluster.get("common_tokens", [])[:5])}."""

    # Embed the cluster description
    try:
        cluster_semantic = await embedder._semantic_embed_one(cluster_desc, "semantic")
    except Exception:
        cluster_semantic = embedder._hash_embed(cluster_desc)

    # Compare to each label template
    matches = []
    for template in CLUSTER_LABEL_TEMPLATES:
        # Template description embedding
        template_text = f"{template['label']}: {template['description']} Examples: {template['examples']}"
        try:
            template_semantic = await embedder._semantic_embed_one(template_text, "semantic")
        except Exception:
            template_semantic = embedder._hash_embed(template_text)

        # Semantic similarity
        sem_sim = embedder.cosine_similarity(
            cluster_semantic[: min(len(cluster_semantic), len(template_semantic))],
            template_semantic[: min(len(cluster_semantic), len(template_semantic))],
        )

        # Signal overlap bonus
        signal_overlap = len(set(signals) & set(template["signals"]))
        signal_bonus = min(signal_overlap / max(1, len(template["signals"])), 0.3)

        combined = sem_sim + signal_bonus

        if combined > 0.4:
            matches.append(
                {
                    "label": template["label"],
                    "description": template["description"],
                    "severity": template["severity"],
                    "confidence": round(min(combined, 0.99), 4),
                    "semantic_sim": round(sem_sim, 4),
                    "signal_overlap": signal_overlap,
                }
            )

    matches.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "top_label": matches[0]["label"] if matches else "unknown",
        "top_confidence": matches[0]["confidence"] if matches else 0.0,
        "all_labels": matches[:3],
        "cluster_size": cluster.get("size", 0),
    }


# ══════════════════════════════════════════════════════════════════════
# CLUSTER SIMILARITY SEARCH
# ══════════════════════════════════════════════════════════════════════


async def find_similar_clusters(
    target_cluster: dict[str, Any],
    min_similarity: float = 0.6,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Find clusters similar to a target cluster using behavioral vector similarity.
    "This cluster looks like the Wintermute cluster from March"
    """
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

    # Embed the target cluster
    target_vec = embed_cluster_profile(target_cluster)
    len(target_vec)

    # Search in Qdrant
    store = await get_qdrant_store()
    results = await store.search(
        target_vec,
        collection="wallet_clusters",
        limit=limit,
        min_similarity=min_similarity,
    )

    return results


async def find_similar_bundles(
    target_bundle: dict[str, Any],
    min_similarity: float = 0.6,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find bundles similar to a target bundle."""
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

    target_vec = embed_bundle_profile(target_bundle)
    store = await get_qdrant_store()
    return await store.search(
        target_vec,
        collection="bundle_patterns",
        limit=limit,
        min_similarity=min_similarity,
    )


# ══════════════════════════════════════════════════════════════════════
# NL → CLUSTER SEARCH
# ══════════════════════════════════════════════════════════════════════


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
    from rmi_rag.crypto_embeddings import get_embedder
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

    embedder = await get_embedder()

    # Embed the NL query
    query_vec = await embedder._semantic_embed_one(f"Wallet cluster with behavior: {query}", "semantic")

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


# ══════════════════════════════════════════════════════════════════════
# FULL BUNDLE/CLUSTER → RAG PIPELINE
# ══════════════════════════════════════════════════════════════════════


async def index_bundle_detection(bundle: dict[str, Any]) -> str:
    """
    After bundle detection runs, index the result in RAG.
    Store bundle behavioral vector + metadata for future similarity search.
    """
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

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
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

    vec = embed_cluster_profile(cluster)

    cluster_id = str(cluster.get("cluster_id", cluster.get("id", "")))
    if not cluster_id:
        cluster_id = hashlib.sha256(json.dumps(cluster, sort_keys=True, default=str).encode()).hexdigest()[:16]

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
            "chain": cluster.get("active_chains", ["unknown"])[0] if cluster.get("active_chains") else "unknown",
        },
        source="cluster_detector",
        severity=labels["all_labels"][0]["severity"] if labels["all_labels"] else "medium",
    )

    logger.info("Indexed cluster %s as '%s' (%s)", cluster_id, labels['top_label'], labels['top_confidence'])

    return {
        "cluster_id": cluster_id,
        **labels,
    }


# ══════════════════════════════════════════════════════════════════════
# BULK BACKFILL
# ══════════════════════════════════════════════════════════════════════


async def backfill_label_templates():
    """Index all cluster label templates into pgvector for auto-labeling."""
    from rmi_rag.crypto_embeddings import get_embedder
    from rmi_rag.qdrant_store import get_qdrant_store  # external dependency

    embedder = await get_embedder()
    store = await get_qdrant_store()
    count = 0

    for template in CLUSTER_LABEL_TEMPLATES:
        label_id = hashlib.sha256(f"label_template:{template['label']}".encode()).hexdigest()[:16]
        content = f"LABEL: {template['label']}. {template['description']}. Examples: {template['examples']}. Signals: {', '.join(template['signals'])}."

        try:
            vec = await embedder._semantic_embed_one(content, "semantic")
        except Exception:
            vec = embedder._hash_embed(content)

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

