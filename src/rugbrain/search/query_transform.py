"""Query transformation for retrieval: HyDE + multi-query expansion.

Two additive retrieval-quality techniques flagged missing by the audit:

  * ``hyde_expand`` — HyDE (Hypothetical Document Embeddings): generate a
    hypothetical answer to the query, embed it, and use it as an extra
    retrieval vector. Retrieving against a likely-answer document surfaces
    semantically similar chunks that a bare query embedding misses.
  * ``multi_query_expand`` — rewrite the query into 3-4 technical variations
    (synonyms, entity focus, keyword/lexical phrasing, natural-language
    framing) and retrieve against each variant, fusing the result lists.

Both are designed to work without a live LLM: ``llm_fn`` is optional and
pluggable. When it is absent (or fails), expansion degrades to a no-op —
``multi_query_expand`` returns ``[query]`` and ``hyde_expand`` treats the
query itself as the hypothetical document — so retrieval never breaks.

``llm_fn`` may be sync or async and returns:
  * HyDE: a single ``str`` (the hypothetical answer document);
  * multi-query: a ``list[str]`` of rewritten variations (a single
    comma/semicolon-separated string is also accepted).

Wiring lives in ``rugbrain.search.engine`` (``search_multi_collection`` /
``three_pillar_search``) behind additive ``hyde=False`` / ``multi_query=False``
params, both default OFF.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Cap on the number of retrieval variants produced by multi-query expansion
# (original query + up to 3 LLM rewrites).
MAX_MULTI_QUERY_VARIANTS = 4

# ── Lightweight synonym / alias expansion (curated, no LLM) ─────────────
# Each group is (canonical term, aliases). Used by ``expand_synonyms`` to
# build extra retrieval variants when a query mentions a crypto term under a
# different spelling ("rug pull" vs "honeypot", "flash loan" vs "flashloan",
# "sandwich" vs "MEV/frontrunning", ...). Deliberately small and deterministic.
SYNONYM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rug pull", ("rugpull", "honeypot")),
    ("flash loan", ("flashloan", "flash-loan")),
    ("sandwich", ("mev", "frontrunning", "front-running")),
    ("oracle manipulation", ("price manipulation", "oracle price")),
    ("private key", ("secret key",)),
    ("multisig", ("multi-sig", "multi sig", "gnosis safe")),
)

# Cap on the total number of synonym-expanded retrieval variants (original
# query + matched alias clusters). Keeps embedding cost bounded per query.
_MAX_SYNONYM_VARIANTS = 4


def _glossary_pattern(term: str) -> str:
    """Word-boundary regex for a glossary term, tolerating a simple English
    plural suffix (``s`` / ``es``).

    The old bare ``\\bterm\\b`` missed every plural — "are there honeypots
    here?" produced zero expansion because ``honeypots`` has no trailing
    word boundary before the word-char ``s``. Real queries pluralize these
    terms constantly, so the matcher accepts an optional ``e?s`` tail while
    keeping the leading/trailing boundaries (substring controls like
    "sandwiched" still never match). Detection and substitution share this
    pattern so a plural match is always substituted whole.
    """
    return rf"\b{re.escape(term)}(?:e?s)?\b"


def expand_synonyms(query: str, max_variants: int = _MAX_SYNONYM_VARIANTS) -> list[str]:
    """Expand *query* with curated crypto term aliases (lightweight, no LLM).

    For every ``SYNONYM_GROUPS`` cluster whose canonical term or any alias
    appears in the query (word-boundary match), produce an additional variant
    that surfaces the whole alias cluster — e.g. "rug pull token" becomes
    "rug pull rugpull honeypot token" — so chunks that use a different
    spelling ("honeypot", "rugpull") still match in embedding space.

    Returns ``[query, ...variants]`` (the original query always first),
    capped at ``max_variants``. Queries with no matched terms return
    ``[query]`` — a no-op — so callers can enable this unconditionally.
    """
    q = (query or "").strip()
    if not q:
        return [q]
    variants: list[str] = [q]
    q_lower = q.lower()
    for term, aliases in SYNONYM_GROUPS:
        if len(variants) >= max_variants:
            break
        members = (term, *aliases)
        matched = next((m for m in members if re.search(_glossary_pattern(m), q_lower)), None)
        if matched is None:
            continue
        cluster = list(dict.fromkeys(members))
        if len(cluster) < 2:
            continue
        replacement = " ".join(cluster)
        if replacement.lower() == matched.lower():
            continue
        variant = re.sub(_glossary_pattern(matched), replacement, q, flags=re.IGNORECASE)
        if variant != q and variant not in variants:
            variants.append(variant)
    return variants[:max_variants]


# embed_fn accepts either an async embedder method (``embed_query``) or a
# sync embedding function — both return ``list[float]``.
EmbedFn = Callable[[str], Awaitable[list[float]]] | Callable[[str], list[float]]


async def _invoke_llm(llm_fn: Callable[..., Any] | None, query: str) -> Any:
    """Invoke *llm_fn* (sync or async), returning its result or ``None``.

    Failures are swallowed and logged — expansion must never break retrieval.
    """
    if llm_fn is None:
        return None
    try:
        result = llm_fn(query)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:
        logger.warning("query_transform: llm_fn failed for %r: %s", query[:60], exc)
        return None


async def _embed_text(embed_fn: EmbedFn, text: str) -> list[float]:
    """Embed *text* with *embed_fn*, supporting sync or async callables."""
    result = embed_fn(text)
    if inspect.isawaitable(result):
        result = await result
    return result


async def hyde_expand(
    query: str,
    embed_fn: EmbedFn,
    llm_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """HyDE expansion: hypothetical document + its embedding.

    Parameters
    ----------
    query:
        Original user query.
    embed_fn:
        Callable embedding text into a vector — normally the embedder's
        ``embed_query``. Sync or async; used to embed the hypothetical
        answer document.
    llm_fn:
        Optional LLM callable producing the hypothetical answer document
        (sync or async). When absent or failing, the query itself is used
        as the hypothetical document (no-op fallback).

    Returns
    -------
    dict with ``hypothetical_doc`` (str), ``embedding`` (list[float]),
    ``query`` (str) and ``used_llm`` (bool).
    """
    hypo: Any = await _invoke_llm(llm_fn, query)
    used_llm = bool(hypo and isinstance(hypo, str) and hypo.strip())
    hypo_doc = hypo if used_llm else query
    embedding = await _embed_text(embed_fn, hypo_doc)
    return {
        "query": query,
        "hypothetical_doc": hypo_doc,
        "embedding": embedding,
        "used_llm": used_llm,
    }


async def multi_query_expand(
    query: str,
    llm_fn: Callable[..., Any] | None = None,
) -> list[str]:
    """Multi-query expansion: rewrite the query into technical variations.

    Parameters
    ----------
    query:
        Original user query.
    llm_fn:
        Optional LLM callable returning a list of rewritten variations (sync
        or async; a single comma/semicolon-separated string is accepted).
        When absent or failing, returns ``[query]`` — the no-op fallback.

    Returns
    -------
    list[str] with the original query first, followed by up to 3 unique
    rewrites (capped at ``MAX_MULTI_QUERY_VARIANTS``).
    """
    variants = [query]
    raw = await _invoke_llm(llm_fn, query)

    if isinstance(raw, (list, tuple)):
        for v in raw:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if not v or v in variants:
                continue
            variants.append(v)
            if len(variants) >= MAX_MULTI_QUERY_VARIANTS:
                break
    elif isinstance(raw, str) and raw.strip():
        # Some providers return a single comma/semicolon-separated string.
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        for p in parts:
            if p not in variants:
                variants.append(p)
            if len(variants) >= MAX_MULTI_QUERY_VARIANTS:
                break

    return variants
