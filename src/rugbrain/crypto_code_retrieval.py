"""Crypto-code retrieval over the ContextCrypto Qdrant collections.

Searches two read-only collections that live on the same Qdrant instance
as the RugBrain collections (populated by the ContextCrypto ingest):

  * ``codebase_reference`` — crypto library code snippets tagged with
    ``library_id`` / ``library_name`` / ``verdict`` (clean | malicious |
    defense | weakness | mixed | unknown), ``file_path``, ``snippet_type``,
    ``language``, ``category``, ``ecosystem``.
  * ``security_patterns`` — exploit PoCs / defense patterns with
    ``category``, ``severity``, ``ecosystem``, ``verdict``, ``tags``.

Both are 2560-dim Cosine with a single unnamed dense vector — the same
dimension as the RugBrain embedder (qwen3-embedding-4b), so the existing
embedder service embeds queries directly.

Retrieval is hybrid: dense vector search per collection + a lightweight
BM25-style keyword score over the candidate payloads, fused with
Reciprocal Rank Fusion (``rugbrain.rrf_fusion._rrf_fuse``). Results carry
full source attribution (library, file path, verdict label, reason_why).

This module talks to Qdrant REST directly (not ``QdrantVectorStore``)
because the store's result mapping drops the ContextCrypto payload fields
(``code_block``, ``library_id``, ...) that attribution needs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from rugbrain.rrf_fusion import _rrf_fuse

logger = logging.getLogger(__name__)

CODEBASE_REFERENCE_COLLECTION = "codebase_reference"
SECURITY_PATTERNS_COLLECTION = "security_patterns"
CONTEXTCRYPTO_COLLECTIONS: tuple[str, ...] = (
    CODEBASE_REFERENCE_COLLECTION,
    SECURITY_PATTERNS_COLLECTION,
)

# Human-readable verdict labels for attribution.
VERDICT_LABELS: dict[str, str] = {
    "clean": "reference implementation (clean)",
    "malicious": "exploit / malicious pattern",
    "defense": "defensive pattern",
    "weakness": "known weakness",
    "mixed": "mixed verdict",
    "unknown": "unclassified",
}

# Lightweight keyword classifier — no new model dependency. Word-boundary
# regexes over contract languages, exploit vocabulary, and testing tools.
_CRYPTO_CODE_TERM_RE = re.compile(
    r"\b("
    r"solidity|solana|rust|move|vyper|cairo|anchor|foundry|hardhat|"
    r"reentrancy|honeypot|rugpull|rug\s?pull|flash\s?loan|oracle\s?manipulation|"
    r"sandwich|frontrunning|mev|slippage|overflow|underflow|"
    r"erc-?20|erc-?721|erc-?1155|spl|pda|cpi|idl|abi|"
    r"uniswap|aave|compound|curve|jupiter|raydium|"
    r"forge\s?test|cargo\s?test|pytest|slither|mythril|echidna|"
    r"smart\s?contract|deployer|mint\s?function|hidden\s?mint|"
    r"tx\.origin|selfdestruct|delegatecall"
    r")\b",
    re.IGNORECASE,
)
_CRYPTO_CODE_HINTS = (
    "how does the contract",
    "how does this contract",
    "contract pattern",
    "exploit code",
    "poc for",
    "proof of concept",
    "audit finding",
    "security pattern",
    "test the contract",
    "write a test for",
)


def classify_crypto_code_query(query: str) -> bool:
    """Return True when *query* looks like a crypto-CODE question.

    Lightweight keyword classifier (contract languages, exploit vocabulary,
    testing tools) — no model dependency. General RAG queries (prices,
    wallets, news, scam checks) return False and keep the existing path.
    """
    q = (query or "").strip().lower()
    if not q:
        return False
    if any(hint in q for hint in _CRYPTO_CODE_HINTS):
        return True
    return bool(_CRYPTO_CODE_TERM_RE.search(q))


async def _resolve_base_url() -> str:
    """Async resolve the Qdrant base URL via the shared fallback chain."""
    from rugbrain.qdrant_store import _resolve_qdrant_url

    return await _resolve_qdrant_url()


def _keyword_score(query: str, payload: dict[str, Any]) -> float:
    """BM25-ish keyword overlap between *query* and a candidate payload.

    Not a true BM25 (no IDF/corpus stats) — a term-coverage ratio over the
    searchable text fields, used as the second fusion list.
    """
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
    if not terms:
        return 0.0
    haystack = " ".join(
        str(payload.get(f, "") or "")
        for f in ("title", "description", "code_block", "content", "tags", "library_id")
    ).lower()
    hits = sum(1 for t in terms if t in haystack)
    return hits / len(terms)


def _verdict_label(verdict: str) -> str:
    return VERDICT_LABELS.get((verdict or "unknown").lower(), VERDICT_LABELS["unknown"])


def _format_hit(
    point: dict[str, Any],
    collection: str,
    dense_score: float,
    keyword_score: float,
) -> dict[str, Any]:
    """Normalize a raw Qdrant point into an attributed result."""
    payload = point.get("payload", {}) or {}
    provenance = payload.get("provenance") or {}
    verdict = str(payload.get("verdict") or "unknown").lower()
    result: dict[str, Any] = {
        "id": point.get("id"),
        "collection": collection,
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "code_block": payload.get("code_block") or payload.get("content", ""),
        "library": payload.get("library_id") or payload.get("library_name") or "",
        "file_path": payload.get("file_path", ""),
        "snippet_type": payload.get("snippet_type", ""),
        "language": payload.get("language", ""),
        "verdict": verdict,
        "verdict_label": _verdict_label(verdict),
        "category": payload.get("category", ""),
        "severity": payload.get("severity", ""),
        "ecosystem": payload.get("ecosystem", ""),
        "source_url": payload.get("source") or provenance.get("source_url", ""),
        "dense_score": round(dense_score, 4),
        "keyword_score": round(keyword_score, 4),
    }
    reason_why = payload.get("reason_why")
    if reason_why:
        result["reason_why"] = reason_why
    return result


class CryptoCodeRetriever:
    """Hybrid retriever over the ContextCrypto collections."""

    def __init__(
        self,
        embedder: Any | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._embedder = embedder
        self._base_url = base_url
        self._timeout = timeout

    async def _get_embedder(self) -> Any:
        if self._embedder is None:
            from rugbrain.embedder import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    async def _get_base_url(self) -> str:
        if self._base_url is None:
            self._base_url = await _resolve_base_url()
        return self._base_url

    async def _search_collection(
        self,
        client: httpx.AsyncClient,
        collection: str,
        query_embedding: list[float],
        limit: int,
        qdrant_filter: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Dense search one collection; returns raw points (empty on failure)."""
        payload: dict[str, Any] = {
            "vector": query_embedding,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if qdrant_filter:
            payload["filter"] = qdrant_filter
        try:
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning(
                    "crypto_code_search_failed collection=%s status=%d body=%s",
                    collection,
                    r.status_code,
                    r.text[:200],
                )
                return []
            return list(r.json().get("result", []))
        except Exception as e:
            logger.warning("crypto_code_search_error collection=%s err=%s", collection, e)
            return []

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        verdict: str | None = None,
        library_id: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        ecosystem: str | None = None,
    ) -> dict[str, Any]:
        """Hybrid crypto-code retrieval with source attribution.

        Dense search both ContextCrypto collections, keyword re-score the
        candidates, RRF-fuse the two rankings, return the top-k attributed
        results.
        """
        if not (query or "").strip():
            return {"query": query, "results": [], "total": 0, "collections": []}

        embedder = await self._get_embedder()
        query_embedding = await embedder.embed_one(query)

        # Structured Qdrant filter (match any provided facet).
        match_fields: dict[str, Any] = {}
        for field, value in (
            ("verdict", verdict),
            ("library_id", library_id),
            ("category", category),
            ("severity", severity),
            ("ecosystem", ecosystem),
        ):
            if value:
                match_fields[field] = value
        qdrant_filter = {"match": match_fields} if match_fields else None

        base_url = await self._get_base_url()
        async with httpx.AsyncClient(base_url=base_url, timeout=self._timeout) as client:
            dense_lists: list[list[dict[str, Any]]] = []
            keyword_lists: list[list[dict[str, Any]]] = []
            raw_by_collection: dict[str, list[dict[str, Any]]] = {}
            for collection in CONTEXTCRYPTO_COLLECTIONS:
                points = await self._search_collection(
                    client, collection, query_embedding, top_k * 2, qdrant_filter
                )
                raw_by_collection[collection] = points
                dense_lists.append(
                    [{"id": p.get("id"), "dense_score": p.get("score", 0)} for p in points]
                )
                kw_ranked = sorted(
                    (
                        {
                            "id": p.get("id"),
                            "keyword_score": _keyword_score(query, p.get("payload") or {}),
                        }
                        for p in points
                    ),
                    key=lambda x: x["keyword_score"],
                    reverse=True,
                )
                keyword_lists.append(kw_ranked)

        # RRF fusion across (dense per collection) + (keyword per collection).
        fused = _rrf_fuse([*dense_lists, *keyword_lists], k=60)
        score_by_id = {f["id"]: f["rrf_score"] for f in fused}

        results: list[dict[str, Any]] = []
        for collection, points in raw_by_collection.items():
            dense_rank = {p.get("id"): i for i, p in enumerate(points)}
            kw_by_id = {
                k["id"]: k["keyword_score"]
                for k in keyword_lists[list(CONTEXTCRYPTO_COLLECTIONS).index(collection)]
            }
            for p in points:
                pid = p.get("id")
                results.append(
                    _format_hit(
                        p,
                        collection,
                        dense_score=1.0 / (60 + dense_rank.get(pid, 60) + 1),
                        keyword_score=kw_by_id.get(pid, 0.0),
                    )
                )
        for r in results:
            r["rrf_score"] = score_by_id.get(r["id"], 0.0)
        results.sort(key=lambda x: x["rrf_score"], reverse=True)
        results = results[:top_k]

        return {
            "query": query,
            "collections": list(CONTEXTCRYPTO_COLLECTIONS),
            "results": results,
            "total": len(results),
            "retriever": "contextcrypto-hybrid",
        }


_retriever: CryptoCodeRetriever | None = None


def get_crypto_code_retriever() -> CryptoCodeRetriever:
    """Process-wide retriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = CryptoCodeRetriever()
    return _retriever


async def search_crypto_code(
    query: str,
    top_k: int = 10,
    verdict: str | None = None,
    library_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    ecosystem: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Public entry point: classify, then retrieve (or report not-routed)."""
    routed = force or classify_crypto_code_query(query)
    if not routed:
        return {
            "query": query,
            "routed": False,
            "results": [],
            "total": 0,
            "note": "query not classified as crypto-code; general RAG unchanged",
        }
    result = await get_crypto_code_retriever().retrieve(
        query,
        top_k=top_k,
        verdict=verdict,
        library_id=library_id,
        category=category,
        severity=severity,
        ecosystem=ecosystem,
    )
    result["routed"] = True
    return result


# ── Route-logic hook for the coding path ──────────────────────────────


async def augment_coding_results(
    query: str, results: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    """Merge ContextCrypto hits into coding-search results when the query
    is a crypto-CODE question. Degrades silently when Qdrant/embedder are
    unavailable so the existing coding path never breaks."""
    if not classify_crypto_code_query(query):
        return results
    try:
        crypto = await search_crypto_code(query, top_k=limit)
    except Exception as e:
        logger.warning("crypto_code_augment_failed err=%s", e)
        return results
    merged = list(results)
    seen = {r.get("id") for r in merged if r.get("id")}
    for r in crypto.get("results", []):
        if r.get("id") in seen:
            continue
        merged.append(
            {
                "content": r.get("code_block") or r.get("description", ""),
                "similarity": r.get("dense_score", 0),
                "source": r.get("source_url", ""),
                "source_collection": r.get("collection"),
                "contextcrypto": True,
                "library": r.get("library", ""),
                "verdict": r.get("verdict", "unknown"),
                "verdict_label": r.get("verdict_label", ""),
                "file_path": r.get("file_path", ""),
                "reason_why": r.get("reason_why"),
                "rrf_score": r.get("rrf_score", 0),
            }
        )
    merged.sort(key=lambda x: x.get("rrf_score", x.get("similarity", 0)), reverse=True)
    return merged
