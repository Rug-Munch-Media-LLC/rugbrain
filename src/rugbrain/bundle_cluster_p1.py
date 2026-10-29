"""bundle_cluster.py split part: logger, embed_bundle_profile."""
from typing import Any
import hashlib
import json
import logging
import numpy as np

logger = logging.getLogger(__name__)

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
    vec[13] = (
        1.0 if bundle.get("earliest_block", 0) == bundle.get("launch_block", 0) else 0.0
    )  # block-0 bundle

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
    for i, chain_name in enumerate(chain_list):
        if chain_name in chain:
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

    return [float(v) for v in vec]
