"""
RAG Search Engine — query expansion helpers (HyDE + multi-query + synonyms).

Additive retrieval-quality techniques (all default OFF): build the list of
retrieval variants for a query — the original query plus, when enabled, a
HyDE hypothetical document and/or multi-query rewrites. The LLM fn is
optional/pluggable; without a live model the expansion degrades to a no-op
(variants == [query]) so existing behavior is preserved.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ExpansionRequest:
    """Parameters for :func:`_build_search_variants` (keeps arity ≤5)."""

    query: str
    hyde: bool = False
    multi_query: bool = False
    embedder: Any = None
    llm_fn: Callable[..., Any] | None = None
    synonym_expand: bool = False


async def _build_search_variants(req: ExpansionRequest) -> tuple[list[str], dict[str, Any]]:
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
    from ..query_transform import expand_synonyms, hyde_expand, multi_query_expand

    query = req.query
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
    if req.synonym_expand:
        for v in expand_synonyms(query):
            if v not in variants:
                variants.append(v)
        meta["synonym_expand"] = True
        meta["synonym_variants"] = max(0, len(variants) - 1)
    if req.hyde:
        hyde_out = await hyde_expand(query, embed_fn=req.embedder.embed_query, llm_fn=req.llm_fn)
        hypo = hyde_out.get("hypothetical_doc") or query
        if hypo not in variants:
            variants.append(hypo)
            meta["hyde_doc"] = hypo
            meta["hyde_embedding"] = hyde_out.get("embedding")
        meta["hyde"] = True
        meta["hyde_used_llm"] = bool(hyde_out.get("used_llm"))
    if req.multi_query:
        for v in await multi_query_expand(query, llm_fn=req.llm_fn):
            if v not in variants:
                variants.append(v)
        meta["multi_query"] = True
    meta["variants"] = len(variants)
    return variants, meta
