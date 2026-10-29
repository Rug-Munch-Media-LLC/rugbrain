"""bundle_cluster.py split part: embed_cluster_profile."""
from typing import Any
import hashlib
import json
import numpy as np

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
    for i, chain_name in enumerate(chain_list):
        if chain_name in [c.lower() for c in chains]:
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

    return [float(v) for v in vec]
