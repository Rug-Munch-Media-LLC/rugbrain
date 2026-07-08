"""
Standalone crypto-aware embedding utility functions.
Extract structural features from contracts, transactions, and wallets.
No API needed — runs locally.
"""

import hashlib
import time
from datetime import datetime

import numpy as np


def extract_contract_features(code: str) -> np.ndarray:
    """
    Extract structural features from smart contract code.
    Returns a 128-dim vector encoding:
    - Opcode/instruction frequency
    - Common rug patterns (honeypot, maxTx, blacklist, mint, ownership)
    - Complexity metrics (cyclomatic, nesting depth)
    - Library/import fingerprint
    """
    vec = np.zeros(128, dtype=np.float32)

    if not code or len(code) < 10:
        return vec

    code_lower = code.lower()

    # ── Rug pull keyword patterns (first 32 dims) ──
    rug_patterns = [
        # Honeypot indicators
        "maxTx",
        "maxtransaction",
        "_maxTxAmount",
        "maxSell",
        "maxBuy",
        "tradingEnabled",
        "tradingActive",
        "enableTrading",
        "isTradingEnabled",
        "blacklist",
        "isBlacklisted",
        "_blacklist",
        "addToBlacklist",
        # Mint/ownership abuse
        "mint",
        "onlyOwner",
        "excludeFromFees",
        "excludeFromReward",
        "setTax",
        "updateTax",
        "setFee",
        "taxFee",
        "liquidityFee",
        # Hidden functions
        "sweep",
        "drain",
        "rug",
        "pullLiquidity",
        "removeLiquidity",
        "manualSwap",
        "manualSend",
        "swapAndLiquify",
        "swapAndLiquify",
        # Proxy / upgrade traps
        "upgradeTo",
        "upgradeToAndCall",
        "changeAdmin",
        "selfdestruct",
        "suicide",
        "delegatecall",
        # Unverified patterns
        "transferOwnership",
        "renounceOwnership",
        "lock",
    ]
    for i, pattern in enumerate(rug_patterns):
        if pattern in code_lower:
            vec[i] = 1.0

    # ── Structural complexity (dims 32-40) ──
    lines = code.count("\n") + 1
    vec[32] = min(lines / 5000.0, 1.0)  # normalized line count
    vec[33] = min(code.count("function ") / 100.0, 1.0)  # function count
    vec[34] = min(code.count("require(") / 50.0, 1.0)  # safety checks
    vec[35] = min(code.count("modifier ") / 20.0, 1.0)  # access control
    vec[36] = min(code.count("event ") / 30.0, 1.0)  # event emissions
    vec[37] = min(code.count("assembly") / 10.0, 1.0)  # low-level code
    vec[38] = min(code.count("for (") + code.count("while ("), 1.0) / 20.0
    vec[39] = min(code.count("if (") / 100.0, 1.0)

    # ── Library/import fingerprint (dims 40-50) ──
    imports = [
        "openzeppelin",
        "uniswap",
        "pancake",
        "sushiswap",
        "chainlink",
        "@openzeppelin",
        "@uniswap",
        "erc20",
        "erc721",
        "ownable",
    ]
    for i, imp in enumerate(imports):
        vec[40 + i] = 1.0 if imp in code_lower else 0.0

    # ── Language fingerprint (dims 50-55) ──
    lang_markers = {
        "solidity": ["pragma solidity", "contract ", "address public"],
        "rust": ["fn ", "pub fn", "struct ", "impl ", "#[account]"],
        "move": ["module ", "public fun", "struct ", "acquires"],
    }
    idx = 50
    for _lang, markers in lang_markers.items():
        score = sum(1 for m in markers if m in code)
        vec[idx] = min(score / len(markers), 1.0)
        idx += 1

    # ── Hash-based fingerprint (dims 56-127) ──
    code_hash = hashlib.sha256(code.encode()).digest()
    for i in range(72):
        vec[56 + i] = (code_hash[i % 32] / 255.0) if i < 72 else 0.0

    return vec


def extract_transaction_features(tx_data: dict) -> np.ndarray:
    """
    Extract numeric features from a single transaction.
    Returns a 64-dim vector encoding:
    - Value, gas metrics
    - Token transfer count
    - Contract interaction patterns
    - Risk signals
    """
    vec = np.zeros(64, dtype=np.float32)

    if not tx_data:
        return vec

    # ── Value & gas (dims 0-9) ──
    value = float(tx_data.get("value_usd", 0) or 0)
    vec[0] = min(np.log1p(value) / 15.0, 1.0)  # log-scaled value
    gas = float(tx_data.get("gas_used", 0) or 0)
    vec[1] = min(gas / 500000.0, 1.0)
    gas_price = float(tx_data.get("gas_price_gwei", 0) or 0)
    vec[2] = min(gas_price / 500.0, 1.0)  # high gas = potential MEV/snipe
    vec[3] = 1.0 if gas_price > 100 else 0.0  # priority gas flag
    vec[4] = 1.0 if tx_data.get("is_swap") else 0.0
    vec[5] = 1.0 if tx_data.get("is_approval") else 0.0
    vec[6] = 1.0 if tx_data.get("involves_contract_creation") else 0.0

    # ── Token interactions (dims 10-19) ──
    token_transfers = int(tx_data.get("token_transfers", 0) or 0)
    vec[10] = min(token_transfers / 50.0, 1.0)
    vec[11] = min(float(tx_data.get("eth_value", 0) or 0) / 100.0, 1.0)
    unique_tokens = int(tx_data.get("unique_tokens", 0) or 0)
    vec[12] = min(unique_tokens / 20.0, 1.0)

    # ── Counterparty analysis (dims 20-29) ──
    counterparties = tx_data.get("counterparties", []) or []
    vec[20] = min(len(counterparties) / 10.0, 1.0)
    tokens_seen: set[str] = set()
    for field in ["from_address", "to_address", "contract_address"]:
        if isinstance(tx_data, dict) and field in tx_data:
                tokens_seen.add(str(tx_data[field]))
    vec[23] = min(len(tokens_seen) / 100.0, 1.0)

    # ── Risk signals (dims 30-39) ──
    risk = tx_data.get("risk_signals", {}) if isinstance(tx_data, dict) else {}
    vec[30] = 1.0 if risk.get("mixer_interaction") else 0.0
    vec[31] = 1.0 if risk.get("sanctioned_counterparty") else 0.0
    vec[32] = 1.0 if risk.get("flash_loan_usage") else 0.0
    vec[33] = min(risk.get("wash_trading_score", 0) / 100.0, 1.0)
    vec[34] = 1.0 if risk.get("sandwich_attack_target") else 0.0
    vec[35] = 1.0 if risk.get("new_wallet", True) else 0.0  # default True = suspicious
    vec[36] = min(risk.get("failed_tx_ratio", 0), 1.0)
    vec[37] = min(risk.get("rug_pull_pattern_score", 0) / 100.0, 1.0)

    # ── Hash fingerprint of counterparty graph (dims 40-63) ──
    cp_sorted = sorted(counterparties)[:50]
    cp_hash = hashlib.sha256(",".join(cp_sorted).encode()).digest()
    for i in range(24):
        vec[40 + i] = cp_hash[i] / 255.0

    return vec


def extract_wallet_features(wallet_data: dict) -> np.ndarray:
    """
    Extract wallet entity features for clustering/scam detection.
    Returns a 64-dim vector.
    """
    vec = np.zeros(64, dtype=np.float32)

    if not wallet_data:
        return vec

    # ── Age & activity (dims 0-9) ──
    created = wallet_data.get("created_at") or wallet_data.get("first_seen")
    if created:
        try:
            if isinstance(created, str):
                created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            else:
                created_ts = float(created)
            age_days = max(0, (time.time() - created_ts) / 86400)
            vec[0] = min(age_days / 365.0, 1.0)  # normalized age
            vec[1] = 1.0 if age_days < 7 else 0.0  # newborn wallet (suspicious)
            vec[2] = 1.0 if age_days < 30 else 0.0  # young wallet
        except (ValueError, TypeError):
            vec[0] = 0.1  # unknown age → slightly suspicious

    # ── Balance/Value (dims 10-19) ──
    balance = float(wallet_data.get("balance_usd", 0) or 0)
    vec[10] = min(np.log1p(balance) / 15.0, 1.0)  # log-scaled balance
    txn_count = int(wallet_data.get("transaction_count", 0) or 0)
    vec[11] = min(txn_count / 10000.0, 1.0)
    vec[12] = min(float(wallet_data.get("avg_txn_value_usd", 0) or 0) / 100000.0, 1.0)

    # ── Risk labels (dims 20-29) ──
    labels = wallet_data.get("labels", []) or []
    risk_tags = {
        "scammer": 20,
        "rug_puller": 21,
        "honeypot_creator": 22,
        "wash_trader": 23,
        "mixer_user": 24,
        "exploiter": 25,
        "sanctioned": 26,
        "phisher": 27,
        "insider": 28,
        "bot": 29,
    }
    for label in labels:
        label_lower = str(label).lower().replace(" ", "_")
        for tag, dim in risk_tags.items():
            if tag in label_lower:
                vec[dim] = 1.0

    # ── Entity cluster hash (dims 30-39) ──
    cluster = str(wallet_data.get("cluster_id", ""))
    if cluster:
        ch = hashlib.sha256(cluster.encode()).digest()
        for i in range(10):
            vec[30 + i] = ch[i] / 255.0

    # ── Chain activity fingerprint (dims 40-49) ──
    chains = wallet_data.get("active_chains", []) or []
    chain_list = [
        "ethereum",
        "solana",
        "base",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "fantom",
        "tron",
    ]
    for i, ch in enumerate(chain_list):
        if ch in [c.lower() for c in chains]:
            vec[40 + i] = 1.0

    # ── Hash of associated addresses (dims 50-63) ──
    assoc = str(wallet_data.get("associated_addresses", ""))
    ah = hashlib.sha256(assoc.encode()).digest()
    for i in range(14):
        vec[50 + i] = ah[i] / 255.0

    return vec
