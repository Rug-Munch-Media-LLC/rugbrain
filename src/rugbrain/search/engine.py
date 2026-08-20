"""
RAG Search Engine — dense, sparse, entity, hybrid search.

Three-pillar hybrid search (dense + sparse + entity) with:
  - Knowledge Graph expansion (Pillar 3+)
  - MMR deduplication after RRF fusion
  - Parent-child retrieval for contextual chunks
  - SPLADE+BM25 sparse search (Pillar 2)
  - Query-adaptive fusion weights

Extracted from search_service.py.
All cross-module deps resolved via search._state / inline imports.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any, cast

from rugbrain.crypto_embeddings import COLLECTIONS, get_embedder
from rugbrain.rrf_fusion import _rrf_fuse
from rugbrain.sparse_search import _sparse_text_search
from rugbrain.splade_bm25 import BM25Index

from ._state import _get_redis
from .ranking import _classify_query

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# SCAM / EXPLOIT KNOWLEDGE COLLECTIONS
# ═══════════════════════════════════════════════════════════════════
# Populated by scripts/ingest_scam_knowledge.py from public exploit/audit
# datasets (DeFiHackLabs, rekt-news, CryptoScamDB, SlowMist, SWC Registry).
# Queries about hacks/exploits/scams are routed here first so the RAG can
# reference specific incidents, techniques, and patterns from day one.
SCAM_COLLECTIONS: tuple[str, ...] = (
    "defi_exploits",
    "scam_patterns",
    "crypto_vulnerabilities",
    # Contract-knowledge expansion: known-good/bad contract code, security
    # patterns, and audit findings (populated by scripts/ingest_codebase_
    # knowledge.py, ingest_security_patterns.py, ingest_good_bad_contracts.py).
    "solidity_patterns",
    "solana_patterns",
    "contract_security",
    "audit_findings",
)

# ═══════════════════════════════════════════════════════════════════
# CODING-KNOWLEDGE COLLECTIONS
# ═══════════════════════════════════════════════════════════════════
# Populated by scripts/ingest_solidity_knowledge.py,
# scripts/ingest_rust_solana_knowledge.py, scripts/ingest_alt_vm_knowledge.py
# and scripts/ingest_security_guides.py. `coding_knowledge` carries
# language/framework/security-guide docs with code snippets; the pattern
# collections carry known-good/bad contract code. Code-intent queries
# ("how to implement X in Solidity", "Rust Solana program example",
# "secure ERC20") are routed here first so RugBrain answers as a
# smart-contract development expert.
CODING_COLLECTIONS: tuple[str, ...] = (
    "coding_knowledge",
    "solidity_patterns",
    "solana_patterns",
    "contract_security",
    "audit_findings",
)

# Keywords that strongly indicate a smart-contract / coding question.
_CODING_QUERY_HINTS = (
    # Languages / ecosystems.
    "solidity",
    "smart contract",
    "smart contracts",
    "erc20",
    "erc721",
    "erc1155",
    "erc4626",
    "erc-20",
    "erc-721",
    "openzeppelin",
    "yul",
    "evm",
    "abi encoding",
    "abi.decode",
    "abi.encode",
    "storage layout",
    "gas optimization",
    "solana",
    "anchor framework",
    "anchor program",
    "program derived address",
    "pda",
    "cpi",
    "cross-program",
    "spl token",
    "token program",
    "associated token",
    "rent exemption",
    "system program",
    "bpf",
    "move language",
    "aptos",
    "sui",
    "cairo",
    "starknet",
    "starknet contract",
    # Intent markers.
    "how to implement",
    "how to write",
    "how to build",
    "how to code",
    "code example",
    "code snippet",
    "in solidity",
    "in rust",
    "in move",
    "in cairo",
    "smart contract development",
    "contract development",
    "reentrancy guard",
    "nonreentrant",
    "checks-effects-interactions",
    "secure erc",
    "secure token",
    "secure contract",
    "contract best practice",
    "resource model",
    "resource-oriented",
    "formal verification",
)

# Keywords that strongly indicate a scam/exploit/intel query. Matching one
# routes the search to SCAM_COLLECTIONS (in addition to any explicit list).
_SCAM_QUERY_HINTS = (
    "hack",
    "hacked",
    "exploit",
    "vulnerab",
    "attack",
    "attacker",
    "rug pull",
    "rugpull",
    "scam",
    "scammer",
    "phishing",
    "phish",
    "fraud",
    "stolen",
    "theft",
    "heist",
    "rekt",
    "breach",
    "vuln",
    "cve-",
    "swc-",
    "flash loan",
    "reentrancy",
    "oracle manipulation",
    "lazarus",
    "siphone",
    "drainer",
    "exploiter",
    "exploit code",
    "poc",
    "proof of concept",
    "how was .* hacked",
)

# ── Code-term regex tier (second detection layer) ──────────────────────
# ``detect_coding_query`` checks the phrase hints above first, then these
# word-boundary regexes so code-intent queries that do not contain a hint
# phrase verbatim ("what is the ABI", "function selector", "write a Python
# SDK wrapper", "how does reentrancy work") still route to code retrieval.
# Strong terms are unambiguous code vocabulary; contextual terms ("code",
# "example", "reentrancy", …) only count when paired with a second code term
# or a "how does" phrasing so exploit-framed queries ("reentrancy hack 2023",
# "exploit code") keep routing to scam/exploit knowledge.
_CODING_TERMS_STRONG = (
    r"solidity|smart\s?contract|contracts?|erc\d{3,4}|openzeppelin|yul|vyper|bytecode|opcode|"
    r"solana|anchor|spl\s?token|associated\s?token|rent\s?exemption|system\s?program|"
    r"program\s?derived\s?address|\bpda\b|\bcpi\b|\bbpf\b|"
    r"move\s?language|aptos|sui|cairo|starknet|rust|python|typescript|javascript|golang|"
    r"\babi\b|\bmodifier\b|\bfunction\b|\bsdk\b|\bsnippet\b|\bsyntax\b|compil(?:e|er|ation)|"
    r"storage\s?layout|gas\s?optimization|formal\s?verification|checks[- ]effects|"
    r"nonreentrant|reentrancy\s?guard|\bmev\b|\bconstructor\b|abi\s?encoding"
)
_CODING_TERMS_CONTEXTUAL = (
    r"interface|library|struct|enum|event|class|def|impl|trait|fn|mod|"
    r"code|example|implementation|program|bot|deploy|script|reentrancy|\bapi\b"
)
_CODING_TERM_RE = re.compile(rf"\b(?:{_CODING_TERMS_STRONG})\b")
_CODING_CTX_RE = re.compile(rf"\b(?:{_CODING_TERMS_CONTEXTUAL})\b")
_HOW_DOES_RE = re.compile(r"\bhow (?:do|does|is|are|can)\b")

# Content cap used for code-intent queries: fenced code blocks must not be
# truncated mid-block by the retrieval enrichment (default prose cap is 500).
_CODE_CONTENT_LIMIT = 8000
# Small similarity bonus applied to code-carrying chunks on code queries —
# enough to float a code chunk above equally-ranked prose, not enough to
# overturn a genuinely better semantic match.
_CODE_CHUNK_BONUS = 0.05

# ═══════════════════════════════════════════════════════════════════
# HYBRID RETRIEVAL (keyword/BM25 lane, additive)
# ═══════════════════════════════════════════════════════════════════
# Opt-in `hybrid=True` path: a lightweight in-memory Okapi BM25 lane runs
# alongside the vector search over the same Redis chunk corpus, and both
# lanes are fused with reciprocal rank fusion (RRF). Defaults are OFF so
# pure-vector behavior is preserved for existing callers.

# Per-collection doc cap for the keyword lane — keeps the per-query BM25
# index lightweight (tokenizing 1k chunks/collection is ~tens of ms).
_HYBRID_KEYWORD_MAX_DOCS = 1000
# Keyword hits fetched per collection relative to the requested limit.
_HYBRID_KEYWORD_LIMIT_FACTOR = 2
# Content preview attached to keyword-only hits (surfaces without vector hit).
_HYBRID_CONTENT_PREVIEW = 500


# ═══════════════════════════════════════════════════════════════════
# SEARCH-QUALITY DEFAULTS (env-overridable)
# ═══════════════════════════════════════════════════════════════════


def _env_bool(name: str, default: bool) -> bool:
    """Parse a 0/1/true/false/yes/no/on/off env var into a bool."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Reranking is ON by default on the main search entry points: the top
# ``rerank_top_k`` fused candidates are re-scored with the self-hosted Qwen3 cross-encoder
# (Qwen3-Reranker-8B) before truncation. The search-lane reranker degrades
# gracefully — when no model backend is installed, or scoring takes longer
# than ``rerank_timeout``, results fall back to their original retrieval
# order with ``rerank_method="none"`` / ``reranked=false`` — a request never
# fails because the reranker is missing or slow.
_RERANK_DEFAULT = _env_bool("RAG_RERANK_ENABLED", True)
# Reranker timeout (seconds). <=0 disables the timeout (score everything).
_RERANK_TIMEOUT = float(os.getenv("RAG_RERANK_TIMEOUT", "2.0"))
# Lightweight curated synonym/alias expansion (default OFF — additive recall;
# see ``rugbrain.search.query_transform.expand_synonyms``). Flip on globally
# with ``RAG_SYNONYM_EXPAND=1`` or per-call with ``synonym_expand=True``.
_SYNONYM_EXPAND_DEFAULT = _env_bool("RAG_SYNONYM_EXPAND", False)
# MMR diversity lambda: 1.0 = pure relevance, 0.0 = pure diversity. 0.6 leans
# relevance while still de-duplicating near-duplicate chunks — deliberately
# NOT over-deduping gold-relevant docs (the eval precision headroom comes from
# the reranker, not from stronger MMR diversity).
_MMR_LAMBDA_DEFAULT = float(os.getenv("RAG_MMR_LAMBDA", "0.6"))


def detect_scam_query(query: str) -> bool:
    """Return True when *query* looks like a scam/exploit knowledge query.

    Used to prioritize SCAM_COLLECTIONS in ``three_pillar_search`` /
    ``search_multi_collection`` when the caller did not pin collections.
    """
    q = (query or "").lower()
    if not q:
        return False
    return any(hint in q for hint in _SCAM_QUERY_HINTS)


def detect_coding_query(query: str) -> bool:
    """Return True when *query* looks like a smart-contract coding question.

    Code-intent queries ("how to implement X in Solidity", "Solana PDA
    security best practices", "secure ERC20", "what is the ABI of a
    function") are routed to CODING_COLLECTIONS first so RugBrain answers
    as a development expert.

    Two tiers: the phrase hints above, then word-boundary code-term regexes.
    Bare ambiguous terms (e.g. "reentrancy", "code") only count when paired
    with a second code term or a "how does" phrasing, so exploit-framed
    queries ("reentrancy hack 2023") keep routing to scam/exploit knowledge.
    """
    q = (query or "").lower()
    if not q:
        return False
    if any(hint in q for hint in _CODING_QUERY_HINTS):
        return True
    if _CODING_TERM_RE.search(q):
        return True
    ctx = list(_CODING_CTX_RE.finditer(q))
    if len(ctx) >= 2:
        return True
    return bool(ctx and _HOW_DOES_RE.search(q))


def resolve_search_collections(
    collections: list[str] | tuple[str, ...] | None,
    query: str | None = None,
) -> list[str]:
    """Resolve the effective collection list for a search.

    - Explicit ``collections`` is returned as-is (caller wins).
    - Otherwise, code-intent queries search CODING_COLLECTIONS first, then the
      default COLLECTIONS; scam/exploit queries search SCAM_COLLECTIONS first;
      general queries use only COLLECTIONS.
    """
    if collections:
        return list(collections)
    if query and detect_coding_query(query):
        return [*CODING_COLLECTIONS, *COLLECTIONS]
    if query and detect_scam_query(query):
        return [*SCAM_COLLECTIONS, *COLLECTIONS]
    return list(COLLECTIONS)


# ═══════════════════════════════════════════════════════════════════
# CODE-CARRYING CHUNK HELPERS
# ═══════════════════════════════════════════════════════════════════
# Shared by the code-query answer path: ``search_coding_knowledge`` flags and
# the code-chunk bias used by ``search_multi_collection`` /
# ``three_pillar_search``. Markers are deliberately loose (contract/function/
# fn/struct/fence) — enough to separate code-carrying chunks from prose.

# Markers that indicate a chunk carries code (contract/function bodies,
# fenced blocks) rather than prose.
_CODE_MARKER_RE = re.compile(
    r"(?:"
    r"pragma\s+solidity|contract\s+\w+|interface\s+\w+|library\s+\w+|"
    r"function\s+\w+|modifier\s+\w+|constructor\s*\(|"
    r"event\s+\w+|error\s+\w+|struct\s+\w+|enum\s+\w+|"
    r"pub\s+fn\b|\bfn\s+\w+|impl\s+\w+|trait\s+\w+|"
    r"\bmod\s+\w+|fun\s+\w+|use\s+\w+::|use\s+anchor|"
    r"\bdef\s+\w+|class\s+\w+|import\s+.*\bfrom\b|```"
    r")",
    re.IGNORECASE,
)

# Fenced markdown code blocks (```lang … ``` or ~~~ … ~~~), language-tagged.
_FENCED_BLOCK_RE = re.compile(r"^([`~]{3,})\s*([\w.+-]*)\s*$(.*?)^\1\s*$", re.MULTILINE | re.DOTALL)


def _is_code_result(result: dict[str, Any]) -> bool:
    """True when *result* carries code content or code-typed metadata."""
    metadata = result.get("metadata") or {}
    doc_type = str(metadata.get("doc_type", "") or "").lower()
    if doc_type in ("contract_code", "code", "solidity", "rust", "solana"):
        return True
    if result.get("has_code_snippet") or result.get("chunk_type") == "code":
        return True
    return bool(_CODE_MARKER_RE.search((result.get("content") or "").lower()))


def bias_toward_code_chunks(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Re-rank *results* so code-carrying chunks float above prose.

    Only applied for code-intent queries (``detect_coding_query``) — non-code
    queries return *results* unchanged (behavior-preserving). A small bonus
    (``_CODE_CHUNK_BONUS``) is added to the similarity score of code chunks,
    and the sort is stable so equal scores keep the retrieval order.
    """
    if not results or not detect_coding_query(query):
        return results

    def _score(r: dict[str, Any]) -> float:
        base = (
            r.get("similarity")
            if r.get("similarity") is not None
            else r.get("rrf_score", 0.0) or 0.0
        )
        return float(base) + (_CODE_CHUNK_BONUS if _is_code_result(r) else 0.0)  # type: ignore[arg-type]

    return sorted(results, key=_score, reverse=True)


def extract_code_blocks(content: str) -> list[dict[str, str]]:
    """Extract fenced code blocks from *content*, intact and language-tagged.

    Each block keeps its original fence marker + language tag so the answer
    path can surface code verbatim instead of flattening it into prose.
    Returns ``[{"language", "code", "fence"}, ...]`` (empty when prose-only).
    """
    blocks: list[dict[str, str]] = []
    for m in _FENCED_BLOCK_RE.finditer(content or ""):
        blocks.append(
            {
                "language": (m.group(2) or "").strip(),
                "code": (m.group(3) or "").strip("\n"),
                "fence": m.group(1),
            }
        )
    return blocks


def format_code_answer(content: str, max_chars: int = 8000) -> str:
    """Return *content* with fenced code blocks surfaced intact.

    Rebuilds each fenced block with its original fence + language tag so
    downstream answer formatting never flattens code into prose. Content
    without fenced code is returned unchanged (trimmed to *max_chars*).
    """
    text = (content or "").strip()
    if not text:
        return ""
    if not _FENCED_BLOCK_RE.search(text):
        return text[:max_chars]
    out: list[str] = []
    pos = 0
    for m in _FENCED_BLOCK_RE.finditer(text):
        prefix = text[pos : m.start()].rstrip()
        if prefix:
            out.append(prefix)
        fence = m.group(1)
        lang = (m.group(2) or "").strip()
        code = (m.group(3) or "").strip("\n")
        out.append(f"{fence}{lang}\n{code}\n{fence}")
        pos = m.end()
    tail = text[pos:].lstrip("\n")
    if tail:
        out.append(tail)
    return "\n\n".join(out)[:max_chars]


# ═══════════════════════════════════════════════════════════════════
# CORE SEARCH
# ═══════════════════════════════════════════════════════════════════


async def search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
    content_limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Semantic search across any collection.
    Uses FAISS ANN index for fast retrieval, with semantic cache.

    ``content_limit`` caps per-result content (see ``_enrich_ann_results``).
    """
    import time

    t0 = time.time()

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # 1. Check semantic cache
    cache_hit = False
    try:
        from rugbrain.compat import get_semantic_cache, SemanticCache  # external dependency

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            cache_hit = True
            logger.info("Semantic cache hit for query: %s", query[:60])
            try:
                from rugbrain.observability import trace_retrieval  # external dependency

                trace_retrieval((time.time() - t0) * 1000, collection, len(cached), cache_hit=True)
            except Exception:
                logger.warning("swallowed exception", exc_info=True)
            return cast(list[dict[str, Any]], cached)
    except Exception as e:
        logger.debug("Semantic cache check skipped: %s", e)

    # 2. Try FAISS ANN index search
    results = []
    try:
        from rugbrain.compat import get_ann_index, AnnIndex  # external dependency

        ann = get_ann_index()
        if ann.is_built(collection):
            ann_results = await ann.search(
                query_embedding=query_vec,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
            if ann_results:
                needs_enrich = [r for r in ann_results if "content" not in r]
                enriched = (
                    await _enrich_ann_results(collection, needs_enrich, content_limit=content_limit)
                    if needs_enrich
                    else []
                )
                results = [r for r in ann_results if "content" in r] + enriched
        else:
            logger.info("FAISS not built for %s, using brute-force fallback", collection)
            results = await embedder.search(
                query=query,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
    except Exception as e:
        logger.warning("ANN/pgvector search failed, falling back to brute-force: %s", e)
        results = await embedder.search(
            query=query,
            collection=collection,
            limit=limit,
            min_similarity=min_similarity,
        )

    # 3. Store in semantic cache
    if results:
        try:
            from rugbrain.compat import get_semantic_cache, SemanticCache  # external dependency

            cache = get_semantic_cache()
            await cache.store(query_vec, results)
        except Exception as e:
            logger.debug("Semantic cache store skipped: %s", e)

    # Apply temporal decay weighting (boost recent content)
    if results:
        try:
            from rugbrain.search.temporal_decay import apply_temporal_decay

            results = apply_temporal_decay(results)
        except Exception:
            pass

    # Trace observability
    try:
        from rugbrain.observability import trace_retrieval  # external dependency

        trace_retrieval((time.time() - t0) * 1000, collection, len(results), cache_hit=cache_hit)
    except Exception:
        logger.warning("swallowed exception", exc_info=True)

    return results


async def _enrich_ann_results(
    collection: str,
    ann_results: list[dict[str, Any]],
    content_limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Given ANN results (id + similarity), fetch full documents from Redis
    and merge the content/metadata.

    ``content_limit`` caps how much content is returned per result; code-intent
    queries pass a larger cap so fenced code blocks are not truncated mid-block.
    """
    if not ann_results:
        return []

    r = await _get_redis()
    pipe = r.pipeline()
    for ar in ann_results:
        pipe.get(f"rag:{collection}:{ar['id']}")

    raw_docs = await pipe.execute()
    enriched = []
    for ar, data in zip(ann_results, raw_docs, strict=False):
        if not data:
            enriched.append(ar)
            continue
        try:
            doc = json.loads(data)
            enriched.append(
                {
                    "id": doc.get("id", ar["id"]),
                    "similarity": ar["similarity"],
                    "content": doc.get("content", "")[:content_limit],
                    "metadata": doc.get("metadata", {}),
                    "model": doc.get("model", ""),
                    "collection": doc.get("collection", collection),
                }
            )
        except json.JSONDecodeError:
            enriched.append(ar)

    return enriched


# ═══════════════════════════════════════════════════════════════════
# HYBRID RETRIEVAL HELPERS (keyword/BM25 lane)
# ═══════════════════════════════════════════════════════════════════
# The keyword lane reuses the existing stdlib-only Okapi BM25 core
# (``rugbrain.splade_bm25.BM25Index``) — no heavy deps, no duplication.
# A fresh in-memory index is built per query over chunk texts read from the
# same Redis keys the vector lane enriches from, so doc ids match exactly
# when RRF-fusing the two lanes.


async def _hybrid_keyword_search(
    query: str,
    collections: list[str],
    limit: int = 20,
    max_docs_per_collection: int = _HYBRID_KEYWORD_MAX_DOCS,
) -> list[dict[str, Any]]:
    """Run the lightweight keyword/BM25 lane for *query* over *collections*.

    Reads chunk docs from Redis (``rag:{collection}:{doc_id}``), builds a
    fresh in-memory Okapi BM25 index, and returns ranked hits with
    ``bm25_score``. Doc ids are un-prefixed so RRF fusion with the vector
    lane (which uses plain doc ids) merges correctly. Returns ``[]`` when
    the corpus is empty or the lane fails — callers degrade to pure vector.
    """
    r = await _get_redis()
    # corpus rows: (doc_id, collection, content, metadata)
    corpus: list[tuple[str, str, str, dict[str, Any]]] = []
    for coll in collections:
        try:
            doc_ids = list(await r.smembers(f"rag:idx:{coll}"))
        except Exception as e:
            logger.debug("hybrid: doc index read failed for %s: %s", coll, e)
            continue
        if not doc_ids:
            continue
        sample = doc_ids[:max_docs_per_collection]
        keys = [f"rag:{coll}:{did}" for did in sample]
        try:
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()
        except Exception as e:
            logger.debug("hybrid: redis fetch failed for %s: %s", coll, e)
            continue
        for did, data in zip(sample, raw_docs, strict=False):
            if not data:
                continue
            try:
                doc = json.loads(data)
            except json.JSONDecodeError:
                continue
            content = doc.get("content", "") or ""
            if not content.strip():
                continue
            corpus.append((str(did), coll, content, doc.get("metadata") or {}))

    if not corpus:
        return []

    def _bm25_rank(
        rows: list[tuple[str, str, str, dict[str, Any]]],
        q: str,
        n: int,
    ) -> list[dict[str, Any]]:
        index = BM25Index(k1=1.2, b=0.75)
        for did, _coll, content, metadata in rows:
            index.add_document(did, content, metadata)
        return index.search(q, limit=n)

    try:
        # Index build + scoring are CPU work — keep the event loop free.
        hits = await asyncio.to_thread(_bm25_rank, corpus, query, limit)
    except Exception as e:
        logger.debug("hybrid: BM25 build/search failed: %s", e)
        return []

    by_id = {did: (coll, content, metadata) for did, coll, content, metadata in corpus}
    results: list[dict[str, Any]] = []
    for hit in hits:
        did = str(hit.get("id", ""))
        coll, content, metadata = by_id.get(did, ("unknown", "", {}))
        results.append(
            {
                "id": did,
                "doc_id": did,
                "bm25_score": float(hit.get("bm25_score", 0.0)),
                "collection": coll,
                "metadata": metadata,
                "content": content[:_HYBRID_CONTENT_PREVIEW],
                "hybrid_source": "bm25",
            }
        )
    return results


def _hybrid_fuse(
    vector_lists: list[list[dict[str, Any]]],
    keyword_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuse per-collection vector results with the keyword lane via RRF.

    Each list is ranked (vector lists by ``similarity``, keyword hits by
    ``bm25_score``) and merged with reciprocal rank fusion
    (``rugbrain.rrf_fusion._rrf_fuse``). Keyword-only hits surface in the
    fused output alongside vector hits; duplicates are merged on doc id.
    """
    ranked_lists: list[list[dict[str, Any]]] = [
        sorted(vlist, key=lambda x: float(x.get("similarity", 0.0) or 0.0), reverse=True)
        for vlist in vector_lists
    ]
    ranked_lists.append(
        sorted(keyword_results, key=lambda x: float(x.get("bm25_score", 0.0) or 0.0), reverse=True)
    )
    return _rrf_fuse(ranked_lists, k=60)


# ═══════════════════════════════════════════════════════════════════
# QUERY EXPANSION HELPERS (HyDE + multi-query)
# ═══════════════════════════════════════════════════════════════════
# Additive retrieval-quality techniques (both default OFF): build the list
# of retrieval variants for a query — the original query plus, when enabled,
# a HyDE hypothetical document and/or multi-query rewrites. The LLM fn is
# optional/pluggable; without a live model the expansion degrades to a no-op
# (variants == [query]) so existing behavior is preserved.


async def _build_search_variants(
    query: str,
    hyde: bool,
    multi_query: bool,
    embedder: Any,
    llm_fn: Callable[..., Any] | None = None,
    synonym_expand: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """Build the retrieval variants for *query* (original first).

    Returns ``(variants, meta)`` where ``meta`` describes the expansion:
    ``{"hyde", "multi_query", "synonym_expand", "synonym_variants",
    "hyde_used_llm", "hyde_embedding", "variants"}``.
    With all flags OFF (the default) this returns ``([query], {"variants": 1})``
    so callers take the existing single-query path unchanged.

    ``meta["hyde_embedding"]`` carries the HyDE hypothetical-doc embedding
    (when HyDE produced a distinct document) so dense search can reuse it
    instead of re-embedding.
    """
    from .query_transform import expand_synonyms, hyde_expand, multi_query_expand

    variants = [query]
    meta: dict[str, Any] = {
        "hyde": False,
        "multi_query": False,
        "synonym_expand": False,
        "synonym_variants": 0,
        "hyde_used_llm": False,
        "hyde_doc": None,
        "hyde_embedding": None,
    }
    if synonym_expand:
        for v in expand_synonyms(query):
            if v not in variants:
                variants.append(v)
        meta["synonym_expand"] = True
        meta["synonym_variants"] = max(0, len(variants) - 1)
    if hyde:
        hyde_out = await hyde_expand(query, embed_fn=embedder.embed_query, llm_fn=llm_fn)
        hypo = hyde_out.get("hypothetical_doc") or query
        if hypo not in variants:
            variants.append(hypo)
            meta["hyde_doc"] = hypo
            meta["hyde_embedding"] = hyde_out.get("embedding")
        meta["hyde"] = True
        meta["hyde_used_llm"] = bool(hyde_out.get("used_llm"))
    if multi_query:
        for v in await multi_query_expand(query, llm_fn=llm_fn):
            if v not in variants:
                variants.append(v)
        meta["multi_query"] = True
    meta["variants"] = len(variants)
    return variants, meta


async def search_multi_collection(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    content_limit: int = 500,
    hybrid: bool = False,
    rerank: bool = _RERANK_DEFAULT,
    rerank_top_k: int = 20,
    rerank_keep: int | None = None,
    rerank_timeout: float = _RERANK_TIMEOUT,
    hyde: bool = False,
    multi_query: bool = False,
    synonym_expand: bool = _SYNONYM_EXPAND_DEFAULT,
    llm_fn: Callable[..., Any] | None = None,
    use_recency_decay: bool = False,
    recency_half_life_days: float = 30.0,
    recency_date_field: str = "published",
) -> list[dict[str, Any]]:
    """Search across multiple collections and merge results.

    When *collections* is None, scam/exploit queries are routed to
    SCAM_COLLECTIONS (then the defaults) so exploit knowledge is prioritised
    automatically; see ``resolve_search_collections``.

    Code-intent queries fetch longer content (``content_limit`` is raised so
    fenced code blocks stay intact) and the merged results are re-ranked so
    code-carrying chunks float above prose (see ``bias_toward_code_chunks``).

    Search-quality parameters:

    * ``rerank`` — **default True**: the top ``rerank_top_k`` candidate
      chunks are re-ranked with a cross-encoder (Qwen3-Reranker-8B) and the best
      ``rerank_keep`` (defaults to *limit*) are kept before truncation.
      Degrades gracefully: when the model backend is unavailable (or scoring
      exceeds ``rerank_timeout``), results fall back to their original
      retrieval order and every result is tagged ``rerank_method="none"`` /
      ``reranked=false`` — the request never fails. Disable with
      ``rerank=False`` or env ``RAG_RERANK_ENABLED=0``.
    * ``rerank_timeout`` — seconds before the reranker falls back to raw
      ranking (default 2.0; env ``RAG_RERANK_TIMEOUT``; ``<=0`` disables).
    * ``hybrid`` — when True, a lightweight keyword/BM25 lane runs alongside
      the vector search and the two lanes are fused with reciprocal rank
      fusion (RRF), surfacing keyword-only hits the vector search missed.
    * ``hyde`` — when True, a HyDE hypothetical document is embedded and
      searched as an extra retrieval vector (see
      ``rugbrain.search.query_transform.hyde_expand``).
    * ``multi_query`` — when True, the query is rewritten into 3-4 technical
      variations and each is searched; per-collection variant results are
      RRF-fused (see ``rugbrain.search.query_transform.multi_query_expand``).
    * ``synonym_expand`` — when True, curated crypto-term aliases ("rug
      pull"→"rugpull/honeypot", "flash loan"→"flashloan", "sandwich"→
      "MEV/frontrunning", ...) are added as extra retrieval variants (see
      ``rugbrain.search.query_transform.expand_synonyms``). Default OFF
      (env ``RAG_SYNONYM_EXPAND=1``).
    * ``llm_fn`` — optional LLM callable for the expansion above (sync or
      async). Absent/failing ⇒ no-op fallback (expansion still works, just
      without model-generated rewrites).
    * ``use_recency_decay`` — when True, a half-life decay on the
      ``published``/date metadata is applied to fused scores so fresher
      documents rank above equally-relevant stale ones (see
      ``rugbrain.search.fusion.apply_recency_decay``).
    * ``recency_half_life_days`` / ``recency_date_field`` — decay half-life
      and metadata date key (defaults 30 days / ``published``).
    """
    collections = resolve_search_collections(collections, query)
    if detect_coding_query(query):
        content_limit = max(content_limit, _CODE_CONTENT_LIMIT)

    # Query expansion (HyDE + multi-query + synonyms) — additive, default OFF.
    if hyde or multi_query or synonym_expand:
        embedder = await get_embedder()
        variants, _expansion_meta = await _build_search_variants(
            query, hyde, multi_query, embedder, llm_fn, synonym_expand=synonym_expand
        )
        expanded = len(variants) > 1
    else:
        variants = [query]
        expanded = False

    async def _search_one(coll: str) -> list[dict[str, Any]]:
        try:
            if not expanded:
                results = await search_similar(
                    query,
                    collection=coll,
                    limit=limit,
                    min_similarity=min_similarity,
                    content_limit=content_limit,
                )
                for r in results:
                    if "collection" not in r:
                        r["collection"] = coll
                return results
            # Expanded: search every variant, then RRF-fuse per collection so
            # the best-of across rewrites wins (bounded to limit * 2).
            variant_lists: list[list[dict[str, Any]]] = []
            for variant in variants:
                results = await search_similar(
                    variant,
                    collection=coll,
                    limit=limit,
                    min_similarity=min_similarity,
                    content_limit=content_limit,
                )
                for r in results:
                    if "collection" not in r:
                        r["collection"] = coll
                if results:
                    variant_lists.append(results)
            if not variant_lists:
                return []
            ranked = [
                sorted(vl, key=lambda x: float(x.get("similarity", 0.0) or 0.0), reverse=True)
                for vl in variant_lists
            ]
            return _rrf_fuse(ranked, k=60)[: limit * 2]
        except Exception as e:
            logger.warning("Search failed for %s: %s", coll, e)
            return []

    # Search all collections in parallel
    all_per_collection = await asyncio.gather(*[_search_one(c) for c in collections])

    if hybrid:
        # Keyword/BM25 lane alongside the vector search; RRF-fuse both lanes.
        keyword_results = await _hybrid_keyword_search(
            query, collections, limit=limit * _HYBRID_KEYWORD_LIMIT_FACTOR
        )
        if keyword_results:
            fused = _hybrid_fuse(list(all_per_collection), keyword_results)
        else:
            fused = [r for results in all_per_collection for r in results]
    else:
        fused = [r for results in all_per_collection for r in results]

    if use_recency_decay and fused:
        from .fusion import apply_recency_decay

        fused = apply_recency_decay(
            fused,
            half_life_days=recency_half_life_days,
            date_field=recency_date_field,
        )

    if rerank and fused:
        from .rerank import rerank_chunks

        try:
            fused = await asyncio.wait_for(
                rerank_chunks(
                    query,
                    fused,
                    top_n=rerank_top_k,
                    keep=rerank_keep if rerank_keep is not None else limit,
                ),
                timeout=None if rerank_timeout <= 0 else rerank_timeout,
            )
        except Exception as e:
            # Reranker unavailable/slow — fall back to raw ranking, never fail.
            logger.warning(
                "search rerank timed out/failed after %.1fs (%s) — falling back to raw ranking",
                rerank_timeout,
                e,
            )
            for r in fused:
                r.setdefault("rerank_method", "none")
                r.setdefault("reranked", False)

    return bias_toward_code_chunks(fused, query)[:limit]


# ═══════════════════════════════════════════════════════════════════
# THREE-PILLAR HYBRID SEARCH
# ═══════════════════════════════════════════════════════════════════


async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    use_mmr: bool = True,
    mmr_lambda: float = _MMR_LAMBDA_DEFAULT,
    use_kg: bool = True,
    use_parent_child: bool = True,
    use_reranker: bool = True,
    use_rating_boosts: bool = True,
    hybrid: bool = False,
    rerank: bool = _RERANK_DEFAULT,
    rerank_top_k: int = 20,
    rerank_keep: int | None = None,
    rerank_timeout: float = _RERANK_TIMEOUT,
    hyde: bool = False,
    multi_query: bool = False,
    synonym_expand: bool = _SYNONYM_EXPAND_DEFAULT,
    llm_fn: Callable[..., Any] | None = None,
    use_recency_decay: bool = False,
    recency_half_life_days: float = 30.0,
    recency_date_field: str = "published",
) -> dict[str, Any]:
    """
    Three-pillar hybrid search with Knowledge Graph expansion, MMR dedup,
    and parent-child retrieval.

      Pillar 1 - Dense vector search via FAISS ANN index (semantic similarity)
      Pillar 2 - Sparse text search via SPLADE+BM25
      Pillar 3 - Entity exact-match + Knowledge Graph expansion
      Post-fusion - MMR deduplication + parent-child context expansion
      Post-rank - Ground News-style rating boosts (when metadata carries a
                  ``rating`` payload) improve quality for news retrieval

    Search-quality parameters (``use_reranker``, ``rerank``, ``use_mmr``
    default ON — each degrades gracefully when its backend is unavailable):

    * ``hyde`` — when True, a HyDE hypothetical document is embedded and
      searched as an extra dense retrieval vector (see
      ``rugbrain.search.query_transform.hyde_expand``).
    * ``multi_query`` — when True, the query is rewritten into 3-4 technical
      variations and each is searched against the dense pillar; per-collection
      variant results are RRF-fused (see
      ``rugbrain.search.query_transform.multi_query_expand``).
    * ``synonym_expand`` — when True, curated crypto-term aliases ("rug
      pull"→"rugpull/honeypot", "flash loan"→"flashloan", "sandwich"→
      "MEV/frontrunning", ...) are added as extra retrieval variants (see
      ``rugbrain.search.query_transform.expand_synonyms``). Default OFF
      (env ``RAG_SYNONYM_EXPAND=1``).
    * ``llm_fn`` — optional LLM callable for the expansion above (sync or
      async). Absent/failing ⇒ no-op fallback.
    * ``use_recency_decay`` — when True, a half-life decay on the
      ``published``/date metadata is folded into the RRF weight so fresher
      documents rank above equally-relevant stale ones (see
      ``rugbrain.search.fusion.rrf_fuse_recency``).
    * ``hybrid`` — when True, a lightweight in-memory keyword/BM25 lane runs
      alongside the pillars and is fused into the RRF as an extra signal,
      surfacing keyword-only chunks the dense/sparse/entity lanes missed.
    * ``mmr_lambda`` — MMR diversity lambda (1.0 = pure relevance, 0.0 =
      pure diversity). Default 0.6 leans relevance so gold-relevant docs are
      not over-deduped; env ``RAG_MMR_LAMBDA``.
    * ``rerank`` — **default True**: the top ``rerank_top_k`` fused
      candidates are re-ranked with the search-lane cross-encoder ( Qwen3 via
      ``rugbrain.search.rerank.rerank_chunks``) and the best ``rerank_keep``
      (defaults to *limit*) are kept. Graceful no-op when the model is not
      installed (results keep their original order; ``search_reranker_applied
      = false`` in ``pillar_summary``). Disable with ``rerank=False`` or env
      ``RAG_RERANK_ENABLED=0``. Independent of ``use_reranker`` (the
      news-lane reranker).
    * ``rerank_timeout`` — seconds before the search-lane reranker falls back
      to raw ranking (default 2.0; env ``RAG_RERANK_TIMEOUT``; ``<=0``
      disables).
    * ``use_reranker`` — the news-lane cross-encoder reranker ( Qwen3 via
      self-hosted Qwen3-Reranker-8B), applied after fusion. Defaults True
      with a graceful no-op fallback: environments without a reranker model
      return results by their original score (``rerank_method="none"``), so
      search never breaks when the model is missing.

    Returns dict with merged results + pillar attribution + MMR metadata.
    """
    collections = resolve_search_collections(collections, query)

    limit = min(limit, 100)

    # Code-intent queries get longer per-result content (fenced code blocks
    # must not be truncated mid-block) and a code-chunk bias at the end.
    is_code_query = detect_coding_query(query)
    content_limit = _CODE_CONTENT_LIMIT if is_code_query else 500

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # Query expansion (HyDE + multi-query + synonyms) — additive, default OFF.
    # Variants are built once and pre-embedded so the dense pillar can search
    # each.
    query_variants, query_expansion_meta = await _build_search_variants(
        query, hyde, multi_query, embedder, llm_fn, synonym_expand=synonym_expand
    )
    expanded = query_expansion_meta["variants"] > 1

    # ── Pillar 1: Dense vector search (ANN) ──
    pillar1_results: list[dict[str, Any]] = []
    try:
        # Primary: the full-featured multi-collection dense search (Qdrant
        # vector + optional hybrid lanes; handles hyde/multi-query). This is
        # the same implementation the search endpoints use. The standalone
        # ANN/compat path below is only a fallback when Qdrant-backed search
        # is unavailable (ImportError, connection failure, etc.).
        pillar1_results = await search_multi_collection(
            query,
            collections,
            limit=limit * 2,
            min_similarity=min_similarity,
            hyde=hyde,
            multi_query=multi_query,
            llm_fn=llm_fn,
        )
        for r in pillar1_results:
            r["pillar"] = "dense"
    except Exception as e:
        logger.warning("Pillar 1 (dense) primary failed: %s — trying ANN fallback", e)
        try:
            from rugbrain.compat import get_semantic_cache, SemanticCache  # external dependency

            cache = get_semantic_cache()
            cached = await cache.check(query_vec)
            if cached is not None:
                pillar1_results = cached
                for r in pillar1_results:
                    r["pillar"] = "dense (cached)"
            else:
                # Pre-embed the variant vectors once (shared across collections).
                # The original query reuses ``query_vec``; the HyDE document
                # reuses its expansion embedding when available.
                if expanded:
                    hyde_doc = query_expansion_meta.get("hyde_doc")
                    hyde_vec = query_expansion_meta.get("hyde_embedding")
                    variant_vecs: list[tuple[str, list[float]]] = [(query, query_vec)]
                    for v in query_variants[1:]:
                        if hyde_doc is not None and v == hyde_doc and hyde_vec is not None:
                            variant_vecs.append((v, hyde_vec))
                        else:
                            variant_vecs.append((v, await embedder.embed_query(v)))
                else:
                    variant_vecs = [(query, query_vec)]

                for coll in collections:
                    try:
                        from rugbrain.compat import get_ann_index, AnnIndex  # external dependency

                        ann = get_ann_index()
                        if expanded:
                            # Search each variant against the collection and
                            # RRF-fuse the per-variant dense lists.
                            variant_lists: list[list[dict[str, Any]]] = []
                            for _v, vec in variant_vecs:
                                ann_hits = await ann.search(
                                    query_embedding=vec,
                                    collection=coll,
                                    limit=limit,
                                    min_similarity=min_similarity,
                                )
                                enriched = await _enrich_ann_results(
                                    coll, ann_hits, content_limit=content_limit
                                )
                                if enriched:
                                    variant_lists.append(enriched)
                            if variant_lists:
                                ranked = [
                                    sorted(
                                        vl,
                                        key=lambda x: float(x.get("similarity", 0.0) or 0.0),
                                        reverse=True,
                                    )
                                    for vl in variant_lists
                                ]
                                fused_coll = _rrf_fuse(ranked, k=60)[: limit * 3]
                                for r in fused_coll:
                                    r["pillar"] = "dense"
                                pillar1_results.extend(fused_coll)
                        else:
                            ann_hits = await ann.search(
                                query_embedding=query_vec,
                                collection=coll,
                                limit=limit,
                                min_similarity=min_similarity,
                            )
                            enriched = await _enrich_ann_results(
                                coll, ann_hits, content_limit=content_limit
                            )
                            for r in enriched:
                                r["pillar"] = "dense"
                            pillar1_results.extend(enriched)
                    except Exception as e:
                        logger.debug("ANN search for %s failed: %s", coll, e)
                        try:
                            hits = await embedder.search(
                                query, coll, limit=limit, min_similarity=min_similarity
                            )
                            for r in hits:
                                r["pillar"] = "dense"
                            pillar1_results.extend(hits)
                        except Exception:
                            logger.warning("swallowed exception", exc_info=True)

                if pillar1_results:
                    await cache.store(query_vec, pillar1_results)
        except Exception:
            logger.warning("swallowed exception", exc_info=True)

    # ── Pillar 2: Sparse text search (BM25 + SPLADE) ──
    pillar2_results: list[dict[str, Any]] = []
    try:
        from rugbrain.splade_bm25 import splade_search

        pillar2_results = await splade_search(query, collections, limit=limit * 2)
        for r in pillar2_results:
            r["pillar"] = "sparse"
    except ImportError:
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except Exception as e:
            logger.warning("Pillar 2 (sparse) fallback failed: %s", e)
    except Exception as e:
        logger.warning("Pillar 2 (SPLADE) failed: %s", e)
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except Exception as e2:
            logger.warning("Pillar 2 fallback also failed: %s", e2)

    # ── Pillar 3: Entity exact-match + Knowledge Graph expansion ──
    pillar3_results: list[dict[str, Any]] = []
    entity_extraction: list[dict[str, Any]] = []
    try:
        from rugbrain.compat import EntityLookup, extract_entities  # external dependency

        entity_extraction = extract_entities(query)
        if entity_extraction:
            lookup = EntityLookup()
            for entity in entity_extraction:
                try:
                    matches = await lookup.lookup(entity["entity"])  # type: ignore[operator]
                    for m in matches:
                        m["pillar"] = "entity"
                    pillar3_results.extend(matches)
                except Exception as e:
                    logger.debug("Entity lookup for %s failed: %s", entity["entity"], e)

            if use_kg:
                # Legacy Redis/Neo4j knowledge-graph lane (app.knowledge_graph)
                # is retired in favor of the Qdrant-native GraphRAG
                # (docs/graphrag-architecture.md). Guard the import so a
                # removed module degrades to ``kg = None`` instead of raising.
                kg = None
                try:
                    from rugbrain.compat import get_knowledge_graph  # external dependency

                    kg = await get_knowledge_graph()  # type: ignore[operator]
                except ImportError:
                    logger.debug("Knowledge Graph module not available, skipping KG expansion")
                    kg = None
                except Exception as e:
                    logger.debug("KG access failed (non-critical): %s", e)
                    kg = None

                if kg is not None:
                    try:
                        kg_results = await kg.expand_query_entities(entity_extraction, max_depth=2)
                        if kg_results:
                            for r in kg_results:
                                r["pillar"] = "entity+kg"
                            pillar3_results.extend(kg_results)
                            logger.info("KG expansion: %s related entities", len(kg_results))
                    except Exception as e:
                        logger.debug("KG expansion failed (non-critical): %s", e)
    except Exception as e:
        logger.warning("Pillar 3 (entity) failed: %s", e)

    # ── Keyword/BM25 lane (hybrid, optional additive signal) ──
    keyword_results: list[dict[str, Any]] = []
    if hybrid:
        try:
            keyword_results = await _hybrid_keyword_search(
                query, collections, limit=limit * _HYBRID_KEYWORD_LIMIT_FACTOR
            )
            for r in keyword_results:
                r["pillar"] = "sparse"
            if keyword_results:
                logger.info("Hybrid keyword lane: %s hits", len(keyword_results))
        except Exception as e:
            logger.warning("Hybrid keyword lane failed (non-critical): %s", e)

    # ── RRF fusion with query-adaptive weights ──
    query_type = _classify_query(query)
    weights = {
        "entity_heavy": (0.3, 0.2, 0.5),
        "keyword_heavy": (0.3, 0.6, 0.1),
        "semantic_heavy": (0.7, 0.2, 0.1),
        "balanced": (0.4, 0.4, 0.2),
    }
    w_dense, w_sparse, w_entity = weights.get(query_type, weights["balanced"])

    p1_ranked = sorted(pillar1_results, key=lambda x: x.get("similarity", 0), reverse=True)
    p2_ranked = sorted(
        pillar2_results,
        key=lambda x: x.get("text_score", 0) or x.get("sparse_score", 0) or x.get("bm25_score", 0),
        reverse=True,
    )
    p3_ranked = sorted(pillar3_results, key=lambda x: x.get("score", 0), reverse=True)

    # Keyword lane joins the fusion as a 4th ranked list when hybrid is on
    # (weighted like the sparse pillar — it is a lexical signal).
    fusion_lists: list[list[dict[str, Any]]] = [p1_ranked, p2_ranked, p3_ranked]
    fusion_weights: list[float] = [w_dense, w_sparse, w_entity]
    if hybrid:
        kw_ranked = sorted(
            keyword_results,
            key=lambda x: x.get("bm25_score", 0) or 0,
            reverse=True,
        )
        fusion_lists.append(kw_ranked)
        fusion_weights.append(w_sparse)

    entity_doc_ids = set()
    for r in pillar3_results:
        did = r.get("doc_id") or r.get("id")
        if did:
            entity_doc_ids.add(did)

    recency_applied = False
    if use_recency_decay:
        from .fusion import rrf_fuse_recency

        fused = rrf_fuse_recency(
            fusion_lists,
            k=60,
            entity_boost=entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
            half_life_days=recency_half_life_days,
            date_field=recency_date_field,
        )
        recency_applied = any(r.get("recency_decay") is not None for r in fused)
        if recency_applied:
            logger.info(
                "Recency-aware fusion: half-life %s days (field=%s)",
                recency_half_life_days,
                recency_date_field,
            )
    else:
        fused = _rrf_fuse(
            fusion_lists,
            k=60,
            entity_boost=entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
        )

    # Add pillar attribution
    pillar_map: dict[str, list[str]] = {}
    for r in pillar1_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "dense" not in pillar_map[did]:
                pillar_map[did].append("dense")
    for r in pillar2_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "sparse" not in pillar_map[did]:
                pillar_map[did].append("sparse")
    for r in pillar3_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "entity" not in pillar_map[did]:
                pillar_map[did].append("entity")

    for r in fused:
        did = r.get("id") or r.get("doc_id")
        did_key = did if isinstance(did, str) else ""
        r["pillars"] = pillar_map.get(did_key, ["dense"])
        r["match_type"] = "+".join(r["pillars"])

    # ── MMR deduplication ──
    pre_mmr_count = len(fused)
    mmr_applied = False
    if use_mmr and len(fused) > limit:
        try:
            from rugbrain.compat import mmr_dedup_results  # external dependency

            fused = await mmr_dedup_results(  # type: ignore[operator]
                fused,
                query_score_field="rrf_score",
                content_field="content",
                lambda_param=mmr_lambda,
                top_k=limit * 3,
            )
            mmr_applied = True
            logger.info(
                "MMR dedup: %s → %s results (lambda=%s)", pre_mmr_count, len(fused), mmr_lambda
            )
        except ImportError:
            logger.debug("MMR dedup module not available, skipping")
        except Exception as e:
            logger.debug("MMR dedup failed (non-critical): %s", e)

    # ── Cross-encoder reranking (optional) ──
    reranker_applied = False
    reranker_method: str | None = None
    if use_reranker and fused:
        for r in fused:
            r["similarity"] = r.get("rrf_score", 0.0)
        pre_rerank = len(fused)

        # Self-hosted Qwen3-Reranker-8B (llama.cpp:8003) — true cross-encoder.
        # Graceful no-op: falls back to raw retrieval order when the server
        # is unreachable (rerank_method="none", reranked=false).
        try:
            from .rerank import rerank_chunks

            reranked = await rerank_chunks(
                query,
                fused,
                top_n=pre_rerank,
                keep=limit * 3,
            )
            if reranked and reranked[0].get("rerank_method", "none") != "none":
                fused = reranked
                reranker_applied = True
                reranker_method = reranked[0].get("rerank_method", "qwen3-reranker-8b")
                logger.info(
                    "Cross-encoder (Qwen3-Reranker-8B) reranked: %d → %d results",
                    pre_rerank,
                    len(fused),
                )
            else:
                logger.debug("Reranker backend unavailable — skipping cross-encoder stage")
        except ImportError:
            logger.debug("rugbrain.search.rerank not available")
        except Exception as e:
            logger.debug("Qwen3 cross-encoder rerank failed (non-critical): %s", e)

    # ── Parent-child context expansion ──
    parent_child_applied = False
    if use_parent_child and fused:
        try:
            expanded_results = []
            for r in fused[: limit * 2]:
                content = r.get("content", "") or ""
                metadata = r.get("metadata", {}) or {}

                parent_id = metadata.get("parent_id") or r.get("parent_id")
                if parent_id:
                    try:
                        coll = r.get("collection", "wallet_profiles")
                        pdoc = await _fetch_parent_context(coll, parent_id)
                        if pdoc:
                            r["parent_content"] = (pdoc.get("content") or "")[:2000]
                            r["parent_id"] = parent_id
                            r["parent_metadata"] = pdoc.get("metadata") or {}
                            parent_child_applied = True
                    except Exception:
                        logger.warning("swallowed exception", exc_info=True)

                if content and len(content) < 800:
                    r["chunk_type"] = "child"
                    r["retrieval_note"] = "Short chunk - consider parent context for generation"

                expanded_results.append(r)

            if expanded_results:
                fused = expanded_results
        except ImportError:
            logger.debug("Contextual chunking module not available")
        except Exception as e:
            logger.debug("Parent-child expansion failed (non-critical): %s", e)

    # ── Parent document context expansion (ParentRetriever) ──
    try:
        from rugbrain.compat import get_parent_retriever  # external dependency

        pr = await get_parent_retriever()  # type: ignore[operator]
        fused = await pr.expand_context(fused)  # type: ignore[union-attr]
        if fused:
            parent_child_applied = True
    except ImportError:
        logger.debug("ParentRetriever module not available")
    except Exception as e:
        logger.debug("ParentRetriever.expand_context failed (non-critical): %s", e)

    # ── Search-lane cross-encoder reranking (default ON, before truncation) ──
    # Re-ranks the top ``rerank_top_k`` fused candidates and keeps the best
    # ``rerank_keep`` (defaults to ``limit``). Independent of ``use_reranker``
    # above — this uses the search-lane reranker with a graceful no-op when
    # the model is not installed, and a timeout fallback to raw ranking when
    # scoring is slow (``search_reranker_applied=false`` in that case).
    search_reranker_applied = False
    search_reranker_method: str | None = None
    if rerank and fused:
        from .rerank import rerank_chunks

        pre_rerank = len(fused)
        search_reranked: list[dict[str, Any]] | None = None
        try:
            search_reranked = await asyncio.wait_for(
                rerank_chunks(
                    query,
                    fused,
                    top_n=rerank_top_k,
                    keep=rerank_keep if rerank_keep is not None else limit,
                ),
                timeout=None if rerank_timeout <= 0 else rerank_timeout,
            )
        except Exception as e:
            # Reranker unavailable/slow — keep raw ranking, never fail.
            logger.warning(
                "search-lane rerank timed out/failed after %.1fs (%s) — keeping raw ranking",
                rerank_timeout,
                e,
            )
            search_reranked = None
        if search_reranked:
            fused = search_reranked
            search_reranker_applied = any(r.get("reranked") for r in search_reranked)
            methods = {r.get("rerank_method") for r in search_reranked if r.get("rerank_method")}
            search_reranker_method = next(iter(methods), None)
            logger.info(
                "Search-lane reranker: %d → %d results (method=%s)",
                pre_rerank,
                len(fused),
                search_reranker_method,
            )

    final_results = bias_toward_code_chunks(fused, query)[:limit]

    # ── Rating boosts (Ground News-style quality reweighting) ──
    # Articles carrying a ``rating`` payload in metadata get boosted (or
    # penalised, e.g. rumors) based on factuality, source reputation,
    # market impact, corroboration and on-chain evidence.
    rating_boosts_applied = False
    if use_rating_boosts and final_results:
        try:
            from rugbrain.ratings import apply_rating_boosts

            final_results = apply_rating_boosts(final_results)
            rating_boosts_applied = any(r.get("rating_boosted") for r in final_results)
        except ImportError:
            logger.debug("rugbrain.ratings not available — skipping rating boosts")
        except Exception as e:
            logger.debug("Rating boost failed (non-critical): %s", e)

    # Code-intent queries keep code chunks on top even when rating boosts ran.
    final_results = bias_toward_code_chunks(final_results, query)

    # ── Confidence scoring ──
    confidence = None
    try:
        # Try our new rugbrain confidence scorer first (standalone)
        from rugbrain.confidence_scorer import score_confidence

        entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
        sources = []
        for r in final_results:
            src = {"type": r.get("type", "unknown"), "score": r.get("score", 0.0)}
            if r.get("metadata", {}).get("ingested_at"):
                import time

                try:
                    from datetime import datetime

                    src["timestamp"] = datetime.fromisoformat(
                        r["metadata"]["ingested_at"].replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, TypeError):
                    pass
            sources.append(src)
        confidence = score_confidence(
            sources,
            query_similarity=final_results[0].get("score", 0.0) if final_results else 0.0,
        )
    except ImportError:
        # Fallback to external rmi-backend confidence module
        try:
            from rugbrain.compat import score_confidence  # external dependency

            entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
            confidence = score_confidence(final_results, query=query, entity_matches=entity_list)
        except ImportError:
            logger.debug("Confidence scoring not available (both modules missing)")
    except Exception as e:
        logger.debug("Confidence scoring failed (non-critical): %s", e)

    # ── Pre-generation reflection: verify on-chain refs before output ──
    reflection = None
    try:
        from rugbrain.pre_generation_reflection import pre_generation_reflection

        reflection = await pre_generation_reflection(query, final_results)
    except Exception as e:
        logger.debug("Pre-generation reflection failed (non-critical): %s", e)

    return {
        "results": final_results,
        "reflection": reflection,
        "pillar_summary": {
            "dense_hits": len(pillar1_results),
            "sparse_hits": len(pillar2_results),
            "entity_hits": len(pillar3_results),
            "entity_doc_ids": list(entity_doc_ids),
            "pillars_used": [
                p
                for p, n in [
                    ("dense", len(pillar1_results)),
                    ("sparse", len(pillar2_results)),
                    ("entity", len(pillar3_results)),
                ]
                if n > 0
            ],
            "mmr_applied": mmr_applied,
            "mmr_lambda": mmr_lambda if mmr_applied else None,
            "pre_mmr_count": pre_mmr_count,
            "post_mmr_count": len(fused) if mmr_applied else pre_mmr_count,
            "kg_expansion": len([r for r in pillar3_results if r.get("pillar") == "entity+kg"]),
            "parent_child_applied": parent_child_applied,
            "reranker_applied": reranker_applied,
            "reranker_method": reranker_method,
            "rating_boosts_applied": rating_boosts_applied,
            "hybrid_keyword_hits": len(keyword_results),
            "search_reranker_applied": search_reranker_applied,
            "search_reranker_method": search_reranker_method,
            "query_expansion": query_expansion_meta,
            "recency_applied": recency_applied,
            "recency_half_life_days": recency_half_life_days if recency_applied else None,
        },
        "entity_extraction": entity_extraction.to_dict() if entity_extraction else None,  # type: ignore[union-attr]
        "query": query,
        "query_type": query_type,
        "fusion_weights": {"dense": w_dense, "sparse": w_sparse, "entity": w_entity},
        "collections": collections,
        "confidence": confidence,
    }


async def _fetch_parent_context(collection: str, parent_id: str) -> dict[str, Any] | None:
    """Fetch a parent window's stored doc for *parent_id*.

    Tries the chunk-keyed layout first (``rag:{collection}:{parent_id}`` —
    written by the parent-child ingest seam in ``search.filters``), then the
    parent-retriever layout (``rag:parent:{parent_id}``). Returns a dict with
    ``content`` / ``metadata``, or ``None`` when neither key exists.
    """
    r = await _get_redis()
    try:
        data = await r.get(f"rag:{collection}:{parent_id}")
        if data:
            doc = json.loads(data)
            return {"content": doc.get("content", ""), "metadata": doc.get("metadata", {})}
    except Exception:
        logger.warning("swallowed exception", exc_info=True)
    try:
        data = await r.get(f"rag:parent:{parent_id}")
        if data:
            doc = json.loads(data)
            return {"content": doc.get("content", ""), "metadata": doc.get("metadata", {})}
    except Exception:
        logger.warning("swallowed exception", exc_info=True)
    return None


# ═══════════════════════════════════════════════════════════════════
# SCAM / EXPLOIT KNOWLEDGE SEARCH
# ═══════════════════════════════════════════════════════════════════


async def search_scam_knowledge(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.4,
) -> dict[str, Any]:
    """Search the scam/exploit knowledge base.

    Defaults to SCAM_COLLECTIONS (defi_exploits, scam_patterns,
    crypto_vulnerabilities). Returns results plus the routed collection list
    so callers can see which knowledge buckets were consulted.
    """
    colls = resolve_search_collections(collections or list(SCAM_COLLECTIONS), query)
    results = await search_multi_collection(
        query, collections=colls, limit=limit, min_similarity=min_similarity
    )
    return {
        "query": query,
        "collections": colls,
        "scam_knowledge": True,
        "results": results,
        "total": len(results),
    }


async def search_coding_knowledge(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.4,
) -> dict[str, Any]:
    """Search the smart-contract coding-knowledge base.

    Defaults to CODING_COLLECTIONS (coding_knowledge, solidity_patterns,
    solana_patterns, contract_security, audit_findings). Results whose content
    carries code (contract/function/struct bodies) are flagged with
    ``has_code_snippet`` and enriched with ``code_blocks`` (fenced blocks
    extracted intact, language-tagged) and ``code_answer`` (the content with
    fenced code surfaced verbatim) so callers can render code-first answers
    without flattening code into prose. Returns results plus the routed
    collection list so callers can see which knowledge buckets were consulted.
    """
    colls = resolve_search_collections(collections or list(CODING_COLLECTIONS), query)
    results = await search_multi_collection(
        query, collections=colls, limit=limit, min_similarity=min_similarity
    )
    for r in results:
        content = r.get("content", "") or ""
        r["has_code_snippet"] = bool(_CODE_MARKER_RE.search(content.lower()))
        r["code_blocks"] = extract_code_blocks(content)
        r["code_answer"] = format_code_answer(content)
    return {
        "query": query,
        "collections": colls,
        "coding_knowledge": True,
        "results": results,
        "total": len(results),
    }


# ═══════════════════════════════════════════════════════════════════
# QUERY TRANSFORMATION SEARCH
# ═══════════════════════════════════════════════════════════════════


async def search_with_transform(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    strategy: str = "auto",
) -> dict[str, Any]:
    """
    Search with query transformation.
    Transforms the query first, then searches with all transformed queries,
    and merges results using RRF.
    """
    from rugbrain.compat import transform_query  # external dependency

    transformed = await transform_query(query, strategy=strategy)  # type: ignore[operator]

    all_result_lists = []
    for tq in transformed.transformed_queries:
        try:
            results = await search_similar(
                tq, collection=collection, limit=limit, min_similarity=0.3
            )
            if results:
                all_result_lists.append(results)
        except Exception as e:
            logger.debug("Transformed query search failed for '%s': %s", tq[:60], e)

    if len(all_result_lists) <= 1:
        results = all_result_lists[0] if all_result_lists else []
        return {
            "query": query,
            "collection": collection,
            "results": results[:limit],
            "total": len(results),
            "strategy": transformed.strategy,
            "transformed_queries": transformed.transformed_queries,
            "metadata": transformed.metadata,
        }

    rrf_k = 60
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for rlist in all_result_lists:
        sorted_list = sorted(rlist, key=lambda x: x.get("similarity", 0), reverse=True)
        for rank, item in enumerate(sorted_list, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = 1.0 / (rrf_k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    merged = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = round(scores[doc_id], 6)
        merged.append(entry)

    return {
        "query": query,
        "collection": collection,
        "results": merged[:limit],
        "total": len(merged),
        "strategy": transformed.strategy,
        "transformed_queries": transformed.transformed_queries,
        "metadata": transformed.metadata,
    }
