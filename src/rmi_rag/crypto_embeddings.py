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

from rmi_rag.bge_embedders import ContractCodeEmbedder, HuggingFaceEmbedder, LocalBGEEmbedder, OllamaBGEEmbedder, OpenRouterEmbedder  # noqa: E402, I001
from rmi_rag.feature_extractors import extract_contract_features, extract_transaction_features, extract_wallet_features  # noqa: E402


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

