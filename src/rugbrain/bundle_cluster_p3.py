"""bundle_cluster.py split part: CLUSTER_LABEL_TEMPLATES, auto_label_cluster, find_similar_clusters."""
from typing import Any
from .bundle_cluster_p2 import embed_cluster_profile  # noqa: F401

CLUSTER_LABEL_TEMPLATES: list[dict[str, Any]] = [
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
    from rugbrain.crypto_embeddings import get_embedder

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
        cluster_semantic = embedder._hash_embed(cluster_desc)  # type: ignore[attr-defined]  # [REVIEW] _hash_embed does not exist on CryptoEmbedder — latent AttributeError fallback

    # Compare to each label template
    matches: list[dict[str, Any]] = []
    for template in CLUSTER_LABEL_TEMPLATES:
        # Template description embedding
        template_text = (
            f"{template['label']}: {template['description']} Examples: {template['examples']}"
        )
        try:
            template_semantic = await embedder._semantic_embed_one(template_text, "semantic")
        except Exception:
            template_semantic = embedder._hash_embed(template_text)  # type: ignore[attr-defined]  # [REVIEW] _hash_embed does not exist on CryptoEmbedder — latent AttributeError fallback

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

async def find_similar_clusters(
    target_cluster: dict[str, Any],
    min_similarity: float = 0.6,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Find clusters similar to a target cluster using behavioral vector similarity.
    "This cluster looks like the Wintermute cluster from March"
    """
    from rugbrain.qdrant_store import get_qdrant_store  # external dependency

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
