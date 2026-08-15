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
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Cap on the number of retrieval variants produced by multi-query expansion
# (original query + up to 3 LLM rewrites).
MAX_MULTI_QUERY_VARIANTS = 4

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
