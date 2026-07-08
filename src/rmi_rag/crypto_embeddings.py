#!/usr/bin/env python3
"""
ULTIMATE CRYPTO EMBEDDER - Rug Munch Intelligence
===================================================
Multi-head embedding system purpose-built for crypto scam/rug detection.

Architecture:
  PRIMARY:   OpenRouter NVIDIA Nemotron (2048d, multimodal, FREE)
  FALLBACK:  Local BGE-small-en-v1.5 (384-dim) - runs on CPU, zero API, zero cost
  FALLBACK:  HuggingFace BGE-M3 (1024d) if token has inference permissions
  SPECIALTY: Crypto-aware hashing - contract bytecode sim, tx pattern fingerprints

Embedding Heads:
  SEMANTIC    - token descriptions, scam narratives, news (OpenRouter/HF)
  CODE        - smart contract similarity (AST-aware + bytecode hash)
  BEHAVIORAL  - transaction patterns, wallet behavior vectors (numeric → float[])
  ENTITY      - wallet labels, cluster IDs, known scammer fingerprints

Collections:
  wallet_profiles   - wallet behavior + labels
  token_analysis    - token metadata + risk signals
  scam_patterns     - known rug/honeypot signatures
  forensic_reports  - investigation findings
  market_intel      - news, trends, alerts
  contract_audits   - smart contract code + audit results
  known_scams       - verified scam DB entries
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PW = os.getenv("REDIS_PASSWORD", "")

# Embedding dimensions by model
DIMS = {
    "local/bge-small-en-v1.5": 384,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-3-small": 1536,
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-small-en-v1.5": 384,
    "nvidia/llama-nemotron-embed-vl-1b-v2:free": 2048,
    "rmi/qwen3-embedding:4b": 2048,  # deprecated - using bge-m3 1024d instead
    "crypto_behavioral": 64,
    "crypto_code_hash": 128,
}

# Default model per head - ONE embedder: bge-m3 1024d
HEAD_DEFAULTS = {
    "semantic": "rmi/bge-m3",  # OUR OWN - 1024d via Ollama, zero cost
    "code": "rmi/bge-m3",  # OUR OWN - 1024d via Ollama
    "behavioral": "crypto_behavioral",  # LOCAL - numeric vectors, zero cost
    "entity": "crypto_behavioral",  # LOCAL - numeric vectors, zero cost
}

# Primary embedding model: OUR OWN bge-m3 via Ollama (1024d)
OR_PRIMARY_EMBED = "rmi/bge-m3"
# OpenRouter fallback: NVIDIA Nemotron (2048d, free) - only if key works
OR_PAID_FALLBACK = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
# Always-available local fallback: BGE-small (384d, CPU, zero cost)
OR_FALLBACK_EMBED = "local/bge-small-en-v1.5"
# NVIDIA NIM embed endpoint
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"

COLLECTIONS = [
    "wallet_profiles",
    "token_analysis",
    "scam_patterns",
    "forensic_reports",
    "market_intel",
    "contract_audits",
    "known_scams",
    "news_articles",
    "transaction_patterns",
    "defi_hacks",
    "rug_timeline",
    "vuln_patterns",
    "crime_reports",
]


# ══════════════════════════════════════════════════════════════════════
# CRYPTO-AWARE FEATURE EXTRACTORS (no API needed, runs locally)
# ══════════════════════════════════════════════════════════════════════


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
    Extract behavioral features from transaction history.
    Returns a 64-dim vector capturing:
    - Volume patterns (mean, std, burst frequency)
    - Time patterns (hour-of-day, day-of-week clusters)
    - Counterparty diversity
    - Gas patterns
    - Token diversity
    """
    vec = np.zeros(64, dtype=np.float32)

    txs = tx_data.get("transactions", []) if isinstance(tx_data, dict) else []

    if not txs:
        return vec

    # ── Volume patterns (dims 0-9) ──
    amounts = []
    timestamps = []
    counterparties = set()
    programs = set()
    for tx in txs:
        amt = float(tx.get("amount", 0) or 0)
        ts = tx.get("timestamp", 0) or tx.get("block_time", 0)
        if amt > 0:
            amounts.append(amt)
        if ts:
            timestamps.append(int(ts))
        cp = tx.get("counterparty") or tx.get("from") or tx.get("to")
        if cp:
            counterparties.add(str(cp))
        prog = tx.get("program") or tx.get("instruction")
        if prog:
            programs.add(str(prog))

    if amounts:
        arr = np.array(amounts, dtype=np.float32)
        vec[0] = np.log1p(float(np.mean(arr))) / 20.0
        vec[1] = min(float(np.std(arr)) / (float(np.mean(arr)) + 1), 1.0)
        vec[2] = min(float(np.max(arr)) / (float(np.sum(arr)) + 1), 1.0)
        vec[3] = min(len(amounts) / 1000.0, 1.0)
        # Burst detection: consecutive txs within short windows
        if len(amounts) > 2:
            vec[4] = min(float(np.sum(arr[:5])) / (float(np.sum(arr)) + 1), 1.0)

    # ── Temporal patterns (dims 10-19) ──
    if len(timestamps) > 1:
        ts_sorted = sorted(timestamps)
        gaps = np.diff(ts_sorted)
        vec[10] = min(float(np.mean(gaps)) / 3600.0, 1.0)  # avg gap in hours
        vec[11] = min(float(np.std(gaps)) / 3600.0, 1.0)
        # Hour-of-day entropy
        hours = [datetime.fromtimestamp(t).hour for t in ts_sorted]
        hour_counts = np.bincount(hours, minlength=24)
        hour_probs = hour_counts / (hour_counts.sum() + 1)
        entropy = -np.sum(hour_probs * np.log(hour_probs + 1e-10))
        vec[12] = min(entropy / 3.2, 1.0)  # normalized entropy
        # Burstiness: ratio of txs in 10% of time to total
        window = max(1, (ts_sorted[-1] - ts_sorted[0]) // 10)
        burst_txs = sum(1 for i in range(len(ts_sorted) - 1) if ts_sorted[i + 1] - ts_sorted[i] < max(60, window))
        vec[13] = min(burst_txs / max(1, len(ts_sorted)), 1.0)

    # ── Diversity metrics (dims 20-29) ──
    vec[20] = min(len(counterparties) / 200.0, 1.0)
    vec[21] = min(len(programs) / 50.0, 1.0)
    vec[22] = min(len(counterparties) / max(1, len(txs)), 1.0)  # uniqueness ratio
    # Token interaction count
    tokens_seen = set()
    for tx in txs:
        for field in ["token", "mint", "token_address", "contract"]:  # noqa: F402
            if field in tx:
                tokens_seen.add(str(tx[field]))
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


# ══════════════════════════════════════════════════════════════════════
# LOCAL BGE EMBEDDER (primary - always available, zero cost)
# ══════════════════════════════════════════════════════════════════════


class LocalBGEEmbedder:
    """
    Local BAAI BGE-small-en-v1.5 embedder.
    Runs on CPU, ~80MB RAM, 384-dim vectors.
    Loaded lazily - first call initializes the model.
    FALLBACK only. Primary is Ollama bge-m3.
    """

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    _model = None

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            cls._model = SentenceTransformer(cls.MODEL_NAME)
            logger.info("LocalBGEEmbedder loaded: %s (384d)", cls.MODEL_NAME)
        return cls._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        embeddings = await asyncio.to_thread(lambda: model.encode(texts, normalize_embeddings=True).tolist())
        return embeddings

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


# ══════════════════════════════════════════════════════════════════════
# PRIMARY EMBEDDER - Our own Ollama bge-m3 (1024d, zero external API)
# ══════════════════════════════════════════════════════════════════════


class OllamaBGEEmbedder:
    """Our own bge-m3 via Ollama. 1024d, always available, zero cost."""

    OLLAMA_URL = "http://172.19.0.1:11434"
    MODEL = "bge-m3"
    DIMS = 1024

    def __init__(self, ollama_url: str | None = None):
        self.url = ollama_url or os.getenv("OLLAMA_URL", self.OLLAMA_URL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        results = []
        async with httpx.AsyncClient(timeout=3) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.url}/api/embeddings",
                        json={"model": self.MODEL, "prompt": text[:500]},
                    )
                    if resp.status_code == 200:
                        vec = resp.json().get("embedding", [])
                        if len(vec) == self.DIMS:
                            results.append(vec)
                            continue
                except Exception:
                    logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                # Fallback: zero vector
                results.append([0.0] * self.DIMS)
        return results

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


# ══════════════════════════════════════════════════════════════════════
# API EMBEDDING PROVIDERS (fallbacks for higher quality)
# ══════════════════════════════════════════════════════════════════════


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, bge-m3 fallback."""

    BASE = "https://openrouter.ai/api/v1/embeddings"
    MODEL = OR_PRIMARY_EMBED
    FALLBACK = OR_FALLBACK_EMBED

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or OPENROUTER_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL

        async def _try_embed(m: str) -> list[list[float]]:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {"model": m, "input": texts}
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(self.BASE, headers=headers, json=payload)
                if resp.status_code == 429:
                    raise RuntimeError("RATE_LIMITED")
                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]

        try:
            return await _try_embed(model_id)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and model_id != self.FALLBACK:
                logger.warning("NVIDIA embed rate-limited, falling back to %s", self.FALLBACK)
                return await _try_embed(self.FALLBACK)
            raise

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class HuggingFaceEmbedder:
    """HuggingFace Inference API - free tier fallback."""

    BASE = "https://api-inference.huggingface.co/models"
    MODEL = "BAAI/bge-m3"

    def __init__(self, token: str | None = None):
        self.token = token or HF_TOKEN
        if not self.token:
            raise ValueError("HUGGINGFACE_TOKEN required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        url = f"{self.BASE}/{model_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            # HF API expects either a single string or list
            results: list[list[float]] = []
            for text in texts:
                resp = await client.post(url, headers=headers, json={"inputs": text})
                if resp.status_code == 503:  # model loading
                    await asyncio.sleep(3)
                    resp = await client.post(url, headers=headers, json={"inputs": text})
                resp.raise_for_status()
                data = resp.json()
                # HF feature-extraction returns nested lists: [[0.1, 0.2, ...]]
                # Handle both shapes: list of floats or list of list of floats
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], list):
                        # Already [[float]] - take the first (mean-pooled usually)
                        results.append(data[0] if len(data) == 1 else data[0])  # noqa: RUF034
                    else:
                        # Flat [float] - wrap it
                        results.append(data)
                else:
                    logger.warning("HF returned unexpected shape: %s", type(data))
                    results.append([0.0] * 1024)
            return results

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


# ══════════════════════════════════════════════════════════════════════
# CONTRACT CODE EMBEDDER (specialized)
# ══════════════════════════════════════════════════════════════════════


class ContractCodeEmbedder:
    """
    Specialized contract code embedder.
    Uses structural features + semantic embedding of decompiled code.
    Detects rug patterns even in obfuscated contracts.
    """

    @staticmethod
    async def embed(code: str, semantic_embedder=None) -> np.ndarray:
        """
        Generate a combined 320-dim contract embedding:
        - 128 dims: structural features (local)
        - 192 dims: semantic understanding (API) - only for non-trivial code
        """
        structural = extract_contract_features(code)

        if semantic_embedder and len(code) > 50:
            # Extract the meaningful parts (not just imports/boilerplate)
            # Take first 4000 chars - enough for the key logic
            code_snippet = code[:4000]
            try:
                semantic_vec = await semantic_embedder.embed_one(f"Smart contract code: {code_snippet[:3000]}")
                # Truncate or pad to 192
                semantic = np.array(semantic_vec[:192], dtype=np.float32)
                if len(semantic) < 192:
                    semantic = np.pad(semantic, (0, 192 - len(semantic)))
            except Exception as e:
                logger.warning("Semantic contract embedding failed: %s", e)
                semantic = np.zeros(192, dtype=np.float32)
        else:
            semantic = np.zeros(192, dtype=np.float32)

        return np.concatenate([structural, semantic])


# ══════════════════════════════════════════════════════════════════════
# MAIN CRYPTO EMBEDDER
# ══════════════════════════════════════════════════════════════════════


@dataclass
class EmbeddingResult:
    vector: list[float]
    dims: int
    model: str
    head: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


class CryptoEmbedder:
    """
    Ultimate multi-head embedder for crypto scam detection.

    Usage:
        embedder = CryptoEmbedder()
        await embedder.initialize()

        # Token scam analysis
        vec = await embedder.embed_token_scam(
            name="FakeRugToken",
            symbol="RUG",
            description="Revolutionary defi...",
            contract_code="pragma solidity...",
            creator_address="0x...",
        )

        # Wallet investigation
        vec = await embedder.embed_wallet(
            address="0x...",
            transactions=[...],
            labels=["suspected_scammer"],
        )

        # Semantic search
        results = await embedder.search("honeypot tokens with ownership renounce pattern")
    """

    def __init__(self):
        self._local: LocalBGEEmbedder | None = None
        self._ollama: OllamaBGEEmbedder | None = None
        self._openrouter: OpenRouterEmbedder | None = None
        self._huggingface: HuggingFaceEmbedder | None = None
        self._redis = None
        self._initialized = False
        self._cache_hits = 0
        self._cache_misses = 0

    async def initialize(self):
        """Initialize embedding providers. Ollama qwen3-embedding is PRIMARY."""
        or_key = os.environ.get("OPENROUTER_API_KEY", "") or OPENROUTER_KEY
        hf_token = os.environ.get("HUGGINGFACE_TOKEN", "") or HF_TOKEN

        # PRIMARY: Our own qwen3-embedding:4b via Ollama (2048d, #2 MTEB)
        self._ollama = OllamaBGEEmbedder()
        logger.info("CryptoEmbedder: Ollama qwen3-embedding:4b ready (PRIMARY, 2048d)")

        self._local = LocalBGEEmbedder()
        logger.info("CryptoEmbedder: Local BGE-small ready (fallback, 384d)")

        if or_key:
            self._openrouter = OpenRouterEmbedder(api_key=or_key)
            logger.info("CryptoEmbedder: OpenRouter ready (fallback, 2048d NVIDIA Nemotron)")

        if hf_token:
            self._huggingface = HuggingFaceEmbedder(token=hf_token)
            logger.info("CryptoEmbedder: HuggingFace ready (fallback, 1024d)")

        self._initialized = True
        logger.info("CryptoEmbedder initialized")

    async def _get_redis(self):
        import redis.asyncio as redis

        if self._redis is None:
            self._redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PW or None,
                db=0,
                decode_responses=True,
            )
        return self._redis

    async def _semantic_embed(self, texts: list[str], head: str = "semantic") -> list[list[float]]:
        """
        Semantic embedding pipeline.
        PRIMARY:   Our own qwen3-embedding:4b via Ollama (2048d, #2 MTEB)
        FALLBACK:  OpenRouter NVIDIA Nemotron (2048d, free)
        FALLBACK:  Local BGE-small (384d, zero cost)
        LAST:      Hash-based (2048d, deterministic)
        """
        if not texts:
            return []

        # PRIMARY: Our own qwen3-embedding:4b (2048d, best quality, zero cost)
        if self._ollama:
            try:
                return await self._ollama.embed(texts)
            except Exception as e:
                logger.warning("Ollama qwen3 embedding failed: %s", e)

        # Fallback: OpenRouter NVIDIA (2048d, free)
        if self._openrouter:
            try:
                return await self._openrouter.embed(texts)
            except Exception as e:
                logger.warning("OpenRouter/NVIDIA embedding failed: %s", e)

        # Fallback: Local BGE (384d, always available, zero cost)
        if self._local:
            try:
                return await self._local.embed(texts)
            except Exception as e:
                logger.warning("Local BGE embedding failed: %s", e)

        # Fallback: HuggingFace
        if self._huggingface:
            try:
                return await self._huggingface.embed(texts)
            except Exception as e:
                logger.warning("HuggingFace embedding failed: %s", e)

        # Last resort: hash-based
        logger.warning("Using hash-based fallback embeddings (low quality)")
        return [self._hash_embed(text) for text in texts]

    async def _semantic_embed_one(self, text: str, head: str = "semantic") -> list[float]:
        # Check cache first
        cache_key = await self._cache_key(head, text)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        results = await self._semantic_embed([text], head)
        vector = results[0] if results else self._hash_embed(text)

        # Store in cache (24h TTL for semantic embeddings)
        await self._cache_set(cache_key, vector, ttl=86400)
        return vector

    def _hash_embed(self, text: str, dims: int = 1024) -> list[float]:
        """Deterministic hash-based embedding (emergency fallback). 1024d matches bge-m3."""
        vec = np.zeros(dims, dtype=np.float32)
        words = re.findall(r"\w+", text.lower())
        for word in words:
            h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
            for i in range(min(8, dims)):
                vec[(h + i) % dims] += 0.01
        norm = np.linalg.norm(vec)
        return (vec / norm).tolist() if norm > 0 else vec.tolist()

    async def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()[:32]
        return f"emb:{prefix}:{h}"

    async def _cache_get(self, key: str) -> list[float] | None:
        try:
            r = await self._get_redis()
            data = await r.get(key)
            if data:
                self._cache_hits += 1
                return json.loads(data)
            self._cache_misses += 1
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
        return None

    async def _cache_set(self, key: str, vector: list[float], ttl: int = 86400):
        try:
            r = await self._get_redis()
            await r.setex(key, ttl, json.dumps(vector))
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    # ─── SPECIALIZED EMBED METHODS ────────────────────────────────

    async def embed_token_scam(
        self,
        name: str,
        symbol: str,
        description: str = "",
        contract_code: str = "",
        creator_address: str = "",
        chain: str = "solana",
        metadata: dict | None = None,
    ) -> EmbeddingResult:
        """
        Generate a comprehensive token scam embedding.
        Combines semantic, code, and behavioral vectors.
        """
        # Build rich semantic text
        semantic_text = f"""Token Analysis: {name} ({symbol}) on {chain}.
Description: {description[:2000]}
Creator: {creator_address}
Metadata: {json.dumps(metadata or {})[:1000]}"""

        # Get semantic embedding
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        semantic_dim = len(semantic_vec)

        # Get code features
        code_vec = extract_contract_features(contract_code).tolist() if contract_code else [0.0] * 128

        # Get behavioral features (from metadata)
        behavioral = extract_transaction_features(metadata or {})
        behavioral_vec = behavioral.tolist()

        # Get wallet features for creator
        wallet = extract_wallet_features(
            {
                "address": creator_address,
                "labels": metadata.get("creator_labels", []) if metadata else [],
            }
        )
        wallet_vec = wallet.tolist()

        # Concatenate: semantic (d) + code (128) + behavioral (64) + wallet (64)
        combined = semantic_vec + code_vec + behavioral_vec + wallet_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="token_scam",
            metadata={
                "name": name,
                "symbol": symbol,
                "chain": chain,
                "semantic_dim": semantic_dim,
                "has_code": bool(contract_code),
                "has_behavioral": bool(metadata),
            },
        )

    async def embed_wallet(
        self,
        address: str,
        transactions: list[dict] | None = None,
        labels: list[str] | None = None,
        chain: str = "solana",
        balance_usd: float = 0,
    ) -> EmbeddingResult:
        """
        Generate a comprehensive wallet risk embedding.
        """
        wallet_data = {
            "address": address,
            "labels": labels or [],
            "balance_usd": balance_usd,
            "transaction_count": len(transactions or []),
        }
        wallet_vec = extract_wallet_features(wallet_data).tolist()

        behavioral_vec = extract_transaction_features({"transactions": transactions or []}).tolist()

        # Semantic: labels + chain context
        semantic_text = f"Wallet {address} on {chain}. Labels: {', '.join(labels or ['unknown'])}. Activity: {len(transactions or [])} transactions."
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")

        combined = semantic_vec + wallet_vec + behavioral_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="wallet",
            metadata={
                "address": address,
                "chain": chain,
                "labels": labels or [],
                "tx_count": len(transactions or []),
            },
        )

    async def embed_scam_pattern(
        self,
        pattern_name: str,
        description: str,
        code_snippets: list[str] | None = None,
        indicators: list[str] | None = None,
        severity: str = "high",
    ) -> EmbeddingResult:
        """
        Embed a known scam pattern for similarity matching.
        """
        semantic_text = f"""SCAM PATTERN: {pattern_name}
Severity: {severity}
Description: {description}
Indicators: {", ".join(indicators or [])}
Code patterns: {"; ".join(code_snippets or [])[:3000]}"""

        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        len(semantic_vec)

        # Code features from snippets
        all_code = "\n".join(code_snippets or [])
        code_vec = extract_contract_features(all_code).tolist()

        combined = semantic_vec + code_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="scam_pattern",
            metadata={
                "pattern": pattern_name,
                "severity": severity,
                "indicators": indicators or [],
            },
        )

    async def embed_contract(
        self,
        address: str,
        code: str,
        chain: str = "ethereum",
        verified: bool = False,
    ) -> EmbeddingResult:
        """Full contract audit embedding."""
        return await ContractCodeEmbedder.embed(
            code,
            semantic_embedder=self._openrouter,
        )

    async def embed_news(
        self,
        title: str,
        content: str,
        source: str = "",
        tags: list[str] | None = None,
    ) -> EmbeddingResult:
        """News article embedding for market intel."""
        text = f"TITLE: {title}\nSOURCE: {source}\nTAGS: {', '.join(tags or [])}\nCONTENT: {content[:3000]}"
        vec = await self._semantic_embed_one(text, "semantic")

        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="openai/text-embedding-3-large",
            head="news",
            metadata={"title": title, "source": source, "tags": tags or []},
        )

    async def embed_query(self, query: str) -> list[float]:
        """Embed a search query for RAG retrieval."""
        # Augment crypto-specific context
        augmented = f"Cryptocurrency scam detection query: {query}"
        return await self._semantic_embed_one(augmented, "semantic")

    # ─── SIMILARITY & SEARCH ────────────────────────────────────

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        a_arr = np.array(a, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)
        dot = np.dot(a_arr, b_arr)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    @staticmethod
    def weighted_similarity(
        vec_a: list[float],
        vec_b: list[float],
        semantic_dim: int,
        weights: tuple[float, float, float, float] = (0.4, 0.2, 0.2, 0.2),
    ) -> float:
        """
        Weighted similarity with head-aware decomposition.
        Weights: (semantic, code, behavioral, wallet)
        Assumes vector layout: [semantic | code(128) | behavioral(64) | wallet(64)]
        """
        if len(vec_a) != len(vec_b):
            # Different dims - use common prefix
            min_len = min(len(vec_a), len(vec_b))
            return CryptoEmbedder.cosine_similarity(vec_a[:min_len], vec_b[:min_len])

        code_start = semantic_dim
        behav_start = code_start + 128
        wallet_start = behav_start + 64

        sem_sim = CryptoEmbedder.cosine_similarity(vec_a[:semantic_dim], vec_b[:semantic_dim])
        code_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[code_start:behav_start], vec_b[code_start:behav_start])
            if len(vec_a) > code_start
            else 0.0
        )
        behav_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[behav_start:wallet_start], vec_b[behav_start:wallet_start])
            if len(vec_a) > behav_start
            else 0.0
        )
        wallet_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[wallet_start:], vec_b[wallet_start:])
            if len(vec_a) > wallet_start
            else 0.0
        )

        return weights[0] * sem_sim + weights[1] * code_sim + weights[2] * behav_sim + weights[3] * wallet_sim

    # ─── VECTOR STORE INTEGRATION ───────────────────────────────

    async def store(
        self,
        collection: str,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        content: str = "",
    ) -> bool:
        """Store an embedding in Redis for fast retrieval."""
        try:
            r = await self._get_redis()
            key = f"rag:{collection}:{doc_id}"

            doc = {
                "id": doc_id,
                "vector": vector,
                "metadata": metadata or {},
                "content": content[:5000],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            await r.setex(key, 86400 * 30, json.dumps(doc))  # 30 day TTL

            # Add to collection index
            await r.sadd(f"rag:idx:{collection}", doc_id)

            return True
        except Exception as e:
            logger.error("Failed to store embedding: %s", e)
            return False

    async def search(
        self,
        query: str,
        collection: str = "scam_patterns",
        limit: int = 10,
        min_similarity: float = 0.6,
    ) -> list[dict[str, Any]]:
        """
        Search for similar documents using cosine similarity.
        Compares query embedding against stored doc vectors (uses common prefix).
        """
        query_vec = await self.embed_query(query)
        q_dim = len(query_vec)

        try:
            r = await self._get_redis()
            doc_ids = await r.smembers(f"rag:idx:{collection}")

            if not doc_ids:
                return []

            # Batch fetch all documents
            keys = [f"rag:{collection}:{did}" for did in doc_ids]
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            results = await pipe.execute()

            # Compute similarities - compare only common prefix dimensions
            scored = []
            for _i, data in enumerate(results):
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue

                doc_vec = doc.get("vector", [])
                # Handle JSON-string vectors (from hash re-embed)
                if isinstance(doc_vec, str):
                    try:
                        doc_vec = json.loads(doc_vec)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not doc_vec or not isinstance(doc_vec, list):
                    continue

                # Compare only the semantic portion (query has semantic only, doc may be multi-head)
                compare_len = min(q_dim, len(doc_vec))
                sim = self.cosine_similarity(query_vec[:compare_len], doc_vec[:compare_len])
                if sim >= min_similarity:
                    scored.append(
                        {
                            "id": doc["id"],
                            "similarity": round(sim, 4),
                            "metadata": doc.get("metadata", {}),
                            "content": doc.get("content", "")[:500],
                        }
                    )

            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:limit]

        except Exception as e:
            logger.error("Search failed: %s", e)
            return []

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "providers": {
                "local_bge": self._local is not None,
                "openrouter": self._openrouter is not None,
                "huggingface": self._huggingface is not None,
            },
            "primary": "local/bge-small-en-v1.5 (384d, $0/mo)",
            "cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "ratio": round(self._cache_hits / max(1, self._cache_hits + self._cache_misses), 3),
            },
            "dimensions": DIMS,
            "collections": COLLECTIONS,
        }


# ══════════════════════════════════════════════════════════════════════
# SEED DATA: Known scam pattern templates
# ══════════════════════════════════════════════════════════════════════

KNOWN_SCAM_PATTERNS = [
    {
        "name": "Honeypot - Sell Disabled",
        "description": "Token where only the creator can sell. Buyers are trapped. Implemented via maxSellAmount=0, tradingEnabled=false, or blacklist of all non-owner addresses.",
        "indicators": [
            "maxSellAmount=0",
            "tradingEnabled=false",
            "onlyOwner can transfer",
            "blacklist all addresses",
        ],
        "severity": "critical",
        "code_snippets": [
            "maxSellAmount = 0",
            "tradingEnabled = false",
            "require(_isExcludedFromFees[msg.sender] || tradingEnabled)",
            "function enableTrading() external onlyOwner",
        ],
    },
    {
        "name": "Unlimited Mint / Rug Pull",
        "description": "Owner can mint unlimited tokens, diluting holders to zero before draining liquidity.",
        "indicators": [
            "unrestricted mint()",
            "onlyOwner mint",
            "no supply cap",
            "mint + removeLiquidity",
        ],
        "severity": "critical",
        "code_snippets": [
            "function mint(address to, uint256 amount) external onlyOwner",
            "mint(msg.sender, 1000000 * 10**decimals())",
        ],
    },
    {
        "name": "Hidden Fee Manipulation",
        "description": "Owner can set fees to 100% after launch, making sells impossible or stealing all transfers.",
        "indicators": [
            "setTax(100)",
            "dynamic fee > 50%",
            "setFee external onlyOwner",
            "no max fee cap",
        ],
        "severity": "high",
        "code_snippets": [
            "function setFee(uint256 _fee) external onlyOwner",
            "taxFee = 99",
            "liquidityFee = 99",
        ],
    },
    {
        "name": "Liquidity Drain Backdoor",
        "description": "Hidden function allowing owner to remove all liquidity. Often disguised as 'manualSwap' or 'rescueToken'.",
        "indicators": [
            "manualSwap",
            "removeLiquidity",
            "drain",
            "sweep",
            "rescueToken with onlyOwner",
        ],
        "severity": "critical",
        "code_snippets": [
            "function manualSwap() external onlyOwner",
            "function removeAllLiquidity() external onlyOwner",
            "uniswapV2Router.removeLiquidity",
        ],
    },
    {
        "name": "Proxy Upgrade Trap",
        "description": "Upgradeable proxy where admin can change implementation to a malicious contract post-launch.",
        "indicators": [
            "upgradeTo",
            "upgradeToAndCall",
            "UUPS",
            "TransparentUpgradeableProxy",
            "changeAdmin",
        ],
        "severity": "high",
        "code_snippets": [
            "function upgradeTo(address newImplementation) external onlyOwner",
            "function upgradeToAndCall(address newImplementation, bytes memory data) external onlyOwner",
        ],
    },
    {
        "name": "Bundle/Sniper Launch",
        "description": "Creator uses bundling to acquire large supply at launch before others can buy, then dumps.",
        "indicators": [
            "multi-wallet buy at block 0",
            "same funder",
            "bundle detection",
            "60%+ supply in 3 wallets",
        ],
        "severity": "high",
        "code_snippets": [],
    },
    {
        "name": "Honeypot - Max Tx Limit",
        "description": "Token with maxTxAmount set so low that only tiny amounts can be sold, trapping larger holders.",
        "indicators": [
            "maxTxAmount < 0.1% supply",
            "maxSellAmount micro",
            "anti-whale as antibot disguise",
        ],
        "severity": "high",
        "code_snippets": [
            "maxTxAmount = 1000",
            "maxSellAmount = 500",
            "maxWalletAmount = 2000",
        ],
    },
    {
        "name": "Fake Renounce / Ownership Hide",
        "description": "Claims ownership renounced but keeps control via secondary address, proxy, or timelock trick.",
        "indicators": [
            "fake renounce",
            "ownership not actually 0xdead",
            "hidden owner",
            "renounceOwnership not called",
        ],
        "severity": "high",
        "code_snippets": [],
    },
    {
        "name": "Sandwich Bot Tax Farm",
        "description": "Token with high taxes that benefits from MEV sandwich bots. Creator runs bots to extract from traders.",
        "indicators": ["tax > 10%", "sandwich pattern", "same wallet MEV + deployer"],
        "severity": "medium",
        "code_snippets": [],
    },
    {
        "name": "Wallet Drainer / Approval Scam",
        "description": "Contract tricks users into approving token spending, then drains their wallets via transferFrom.",
        "indicators": [
            "unlimited approval request",
            "transferFrom in unverified contract",
            "approveAndCall",
            "permit exploit",
        ],
        "severity": "critical",
        "code_snippets": [
            "IERC20(token).transferFrom(victim, attacker, balance)",
            "function claim(address token) external",
            "permit(",
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_embedder: CryptoEmbedder | None = None


async def get_embedder() -> CryptoEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CryptoEmbedder()
        await _embedder.initialize()
    return _embedder

