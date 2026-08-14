#!/usr/bin/env python3
"""
Crypto Embedder - Multi-head embedding system for RAG.

Architecture:
  PRIMARY:   Configurable primary model (set via RAG_PRIMARY_EMBED_MODEL)
  FALLBACK:  Configurable fallback model (set via RAG_FALLBACK_EMBED_MODEL)
  SPECIALTY: Crypto-aware hashing - contract bytecode sim, tx pattern fingerprints

Embedding Heads:
  SEMANTIC    - token descriptions, scam narratives, news
  CODE        - smart contract similarity (AST-aware + bytecode hash)
  BEHAVIORAL  - transaction patterns, wallet behavior vectors
  ENTITY      - wallet labels, cluster IDs, known scammer fingerprints

Collections:
  Configured via RAG_COLLECTIONS env var (comma-separated).
"""

import hashlib
import json
import logging
import os
import re
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

# Embedding dimensions by model.
# Configure via RAG_EMBED_DIMS env var: "model1:dim1,model2:dim2"
# Example: "your-model:1024,fallback:384"
_DIMS_ENV = os.getenv("RAG_EMBED_DIMS", "")
DIMS: dict[str, int] = {}
if _DIMS_ENV:
    for entry in _DIMS_ENV.split(","):
        if ":" in entry:
            model, dim = entry.split(":", 1)
            DIMS[model.strip()] = int(dim.strip())
if not DIMS:
    # Sensible defaults showing the pattern
    DIMS = {
        "bge-m3": 1024,
        "fallback-model": 384,
        "behavioral": 64,
        "code_hash": 128,
    }

# Default model per head.
# Configure via RAG_HEAD_DEFAULTS env var: "head:model,head:model"
# Example: "semantic:your-model,code:your-model"
_HEAD_ENV = os.getenv("RAG_HEAD_DEFAULTS", "")
HEAD_DEFAULTS: dict[str, str] = {}
if _HEAD_ENV:
    for entry in _HEAD_ENV.split(","):
        if ":" in entry:
            head, model = entry.split(":", 1)
            HEAD_DEFAULTS[head.strip()] = model.strip()
if not HEAD_DEFAULTS:
    HEAD_DEFAULTS = {
        "semantic": "your-embedding-model",
        "code": "your-embedding-model",
        "behavioral": "behavioral-embedder",
        "entity": "behavioral-embedder",
    }

# Primary embedding model (set your model name here or via env var)
OR_PRIMARY_EMBED = os.getenv("RAG_PRIMARY_EMBED_MODEL", "openrouter/your-model-here")
# OpenRouter fallback model
OR_PAID_FALLBACK = os.getenv("RAG_FALLBACK_EMBED_MODEL", "openrouter/your-fallback-here")
# Always-available local fallback
OR_FALLBACK_EMBED = os.getenv("RAG_LOCAL_EMBED_MODEL", "local/your-local-model")
# Embed API endpoint (set your preferred provider URL)
NVIDIA_EMBED_URL = os.getenv(
    "RAG_EMBED_URL", "https://your-embed-provider.example.com/v1/embeddings"
)

# Collection names for vector storage.
# Configure via RAG_COLLECTIONS env var (comma-separated).
# Example: RAG_COLLECTIONS="profiles,analysis,patterns"
_COLLECTIONS_ENV = os.getenv("RAG_COLLECTIONS", "")
if _COLLECTIONS_ENV:
    COLLECTIONS = [c.strip() for c in _COLLECTIONS_ENV.split(",")]
else:
    COLLECTIONS = [
        "threat_intel",
        "rmi_agent_memory",
        "rmi_community",
    ]

from rugbrain.bge_embedders import (  # noqa: E402
    ContractCodeEmbedder,
    HuggingFaceEmbedder,
    InfinityBGEEmbedder,
    LocalBGEEmbedder,
    OpenRouterEmbedder,
)
from rugbrain.feature_extractors import (  # noqa: E402
    extract_contract_features,
    extract_transaction_features,
    extract_wallet_features,
)

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
        self._infinity: InfinityBGEEmbedder | None = None
        self._openrouter: OpenRouterEmbedder | None = None
        self._huggingface: HuggingFaceEmbedder | None = None
        self._redis = None
        self._initialized = False
        self._cache_hits = 0
        self._cache_misses = 0

    async def initialize(self):
        """Initialize embedding providers."""
        or_key = os.environ.get("OPENROUTER_API_KEY", "") or OPENROUTER_KEY
        hf_token = os.environ.get("HUGGINGFACE_TOKEN", "") or HF_TOKEN

        # PRIMARY embedding provider
        self._infinity = InfinityBGEEmbedder()
        logger.info("CryptoEmbedder: primary infinity embedder ready")

        self._local = LocalBGEEmbedder()
        logger.info("CryptoEmbedder: local fallback embedder ready")

        if or_key:
            self._openrouter = OpenRouterEmbedder(api_key=or_key)
            logger.info("CryptoEmbedder: OpenRouter embedder ready")

        if hf_token:
            self._huggingface = HuggingFaceEmbedder(token=hf_token)
            logger.info("CryptoEmbedder: HuggingFace embedder ready")

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
        Semantic embedding pipeline with tiered fallback:
        PRIMARY:   Primary embedder (Infinity BAAI/bge-m3)
        FALLBACK:  OpenRouter or API-based embedder
        FALLBACK:  Local embedder (always available)
        FALLBACK:  HuggingFace embedder
        LAST:      Hash-based (low quality, deterministic)
        """
        if not texts:
            return []

        # PRIMARY embedder
        if self._infinity:
            try:
                return await self._infinity.embed(texts)
            except Exception as e:
                logger.warning("Primary infinity embedding failed: %s", e)

        # Fallback: OpenRouter
        if self._openrouter:
            try:
                return await self._openrouter.embed(texts)
            except Exception as e:
                logger.warning("OpenRouter embedding failed: %s", e)

        # Fallback: Local embedder
        if self._local:
            try:
                return await self._local.embed(texts)
            except Exception as e:
                logger.warning("Local embedding failed: %s", e)

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
        """Deterministic hash-based embedding (emergency fallback)."""
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
        code_vec = (
            extract_contract_features(contract_code).tolist() if contract_code else [0.0] * 128
        )

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
            CryptoEmbedder.cosine_similarity(
                vec_a[code_start:behav_start], vec_b[code_start:behav_start]
            )
            if len(vec_a) > code_start
            else 0.0
        )
        behav_sim = (
            CryptoEmbedder.cosine_similarity(
                vec_a[behav_start:wallet_start], vec_b[behav_start:wallet_start]
            )
            if len(vec_a) > behav_start
            else 0.0
        )
        wallet_sim = (
            CryptoEmbedder.cosine_similarity(vec_a[wallet_start:], vec_b[wallet_start:])
            if len(vec_a) > wallet_start
            else 0.0
        )

        return (
            weights[0] * sem_sim
            + weights[1] * code_sim
            + weights[2] * behav_sim
            + weights[3] * wallet_sim
        )

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
                "primary": self._infinity is not None,
                "fallback_api": self._openrouter is not None,
                "local": self._local is not None,
                "huggingface": self._huggingface is not None,
            },
            "cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "ratio": round(self._cache_hits / max(1, self._cache_hits + self._cache_misses), 3),
            },
            "collections_count": len(COLLECTIONS),
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
