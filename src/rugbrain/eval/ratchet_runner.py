"""Measurement runner — dense, hybrid, and rerank layers, read-only.

Deliberately decoupled from the graph layer
-------------------------------------------
The previous nightly died on an ``ImportError`` raised by an unrelated graph
refactor. That is the wrong dependency direction: retrieval quality did not
change, but the monitor was taken out by code that cannot affect it.

This module therefore imports ONLY from ``rugbrain.qdrant_store``,
``rugbrain.embeddings`` and ``rugbrain.search.rerank`` — never the MCP server,
never the graph implementation. A change to graph assembly cannot make a
retrieval monitor unrunnable. The store is constructed here, directly, rather
than fetched through a façade that pulls in the whole server.

Layer definitions
-----------------
dense   ``store.search`` — semantic only.
hybrid  ``store.hybrid_search`` — dense + BM25 sparse with native RRF fusion.
        This is what production serves, so this is what the gate decides on.
rerank  hybrid, then the rerank stage reorders the candidate pool. Reordering
        changes which documents survive truncation, so a corpus that passes
        hybrid can still fail rerank. Both are measured; only hybrid gates.

Read-only: this module calls ``search``/``hybrid_search``/rerank and GETs
collection metadata. It never writes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from rugbrain.eval.baseline_ratchet import Layer, LayerMetrics, Measurement
from rugbrain.eval.ndcg import mrr, ndcg_at_k, recall_at_k

DEFAULT_LIMIT = 40
DEFAULT_CANDIDATE_CAP = 40

# Golden rows carry a target collection; rows naming a collection we do not
# measure are reported as errors rather than silently skipped.
_SKIP_KEYS = frozenset({"__comment__", "notes"})


def corpus_hash(rows: list[dict[str, Any]]) -> str:
    """Fingerprint the measured corpus: ids, queries and gold, sorted.

    A baseline is only comparable against the same corpus. Hashing the
    *content* (not the file mtime) means a reformat that changes nothing
    measurable does not invalidate the baseline, while a real edit — a gold
    chunk rewritten, a case added — does.
    """
    payload = sorted(
        json.dumps(
            {
                "query": r.get("query", ""),
                "collection": r.get("collection"),
                "gold_chunks": sorted(r.get("gold_chunks") or []),
                "relevant_doc_ids": sorted(r.get("relevant_doc_ids") or []),
            },
            sort_keys=True,
        )
        for r in rows
        if not (_SKIP_KEYS & set(r))
    )
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()


def _gold_ids(row: dict[str, Any]) -> list[str]:
    return [str(i) for i in (row.get("relevant_doc_ids") or []) if i]


def _gold_text(row: dict[str, Any]) -> list[str]:
    return [str(c) for c in (row.get("gold_chunks") or []) if c]


def _hit_id(hit: dict[str, Any]) -> str:
    for key in ("id", "doc_id", "uuid", "chunk_id"):
        val = hit.get(key)
        if val is not None:
            return str(val)
    return ""


def _hit_text(hit: dict[str, Any]) -> str:
    return str(hit.get("content") or hit.get("text") or "")


def _score_hits(
    row: dict[str, Any], hits: list[dict[str, Any]], k_values: tuple[int, ...]
) -> dict[str, float]:
    """Score one query's ranked hits against its gold.

    Exact id match when the row carries ``relevant_doc_ids``; otherwise a
    case-insensitive substring match of ``gold_chunks`` against the hit text.
    Ids are not stable across re-ingests, so text matching is the fallback
    rather than a silently weaker default.
    """
    gold_ids = set(_gold_ids(row))
    gold_text = [g.lower() for g in _gold_text(row)]
    ranked: list[bool] = []
    for hit in hits:
        hit_id = _hit_id(hit)
        if gold_ids:
            ranked.append(hit_id in gold_ids)
            continue
        haystack = _hit_text(hit).lower()
        ranked.append(any(g and g in haystack for g in gold_text))

    out: dict[str, float] = {}
    for k in k_values:
        out[f"recall_at_{k}"] = recall_at_k(ranked, k)
    out["mrr"] = mrr(ranked)
    out["ndcg_at_10"] = ndcg_at_k(ranked, 10)
    return out


def _average(values: list[dict[str, float]]) -> LayerMetrics:
    if not values:
        return LayerMetrics()
    keys = values[0].keys()
    return LayerMetrics(**{k: round(sum(v[k] for v in values) / len(values), 4) for k in keys})


def _make_dense_retriever(store: Any, limit: int) -> Any:
    async def _dense(query: str, collection: str) -> list[dict[str, Any]]:
        from rugbrain.embeddings import get_query_embedding

        vector = await get_query_embedding(query)
        return await store.search(collection, vector, limit=limit)

    return _dense


def _make_hybrid_retriever(store: Any, limit: int) -> Any:
    async def _hybrid(query: str, collection: str) -> list[dict[str, Any]]:
        from rugbrain.embeddings import get_query_embedding

        vector = await get_query_embedding(query)
        return await store.hybrid_search(
            query_text=query,
            query_embedding=vector,
            collection=collection,
            limit=limit,
        )

    return _hybrid


async def _rerank(query: str, hits: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Apply the rerank stage to a candidate pool, tolerating its absence.

    Rerank is best-effort by design: ``search/rerank.py`` returns documents in
    their original order when the reranker is unreachable. That is correct
    production behaviour but it would silently make the rerank layer a copy of
    hybrid, so the applied method is reported and an absent rerank is visible
    in the measurement rather than hidden.
    """
    from rugbrain.search import rerank as rerank_mod

    if not rerank_mod.RERANKER_ENABLED:
        return hits
    try:
        out = await rerank_mod.rerank(query, list(hits[:cap]))
    except Exception:  # noqa: BLE001 - rerank failure must not kill the eval
        return hits
    if not isinstance(out, list) or len(out) != len(hits[:cap]):
        # A length-mismatched order is discarded in silence downstream and
        # looks exactly like "rerank ran and did nothing".
        return hits
    return out


async def measure(
    rows: list[dict[str, Any]],
    layers: tuple[Layer, ...] = ("dense", "hybrid", "rerank"),
    limit: int = DEFAULT_LIMIT,
    candidate_cap: int = DEFAULT_CANDIDATE_CAP,
    dim_checks: list[dict[str, Any]] | None = None,
    default_collection: str = "codebase_reference_v2",
) -> Measurement:
    """Measure recall@5/@10, MRR and nDCG@10 on each requested layer.

    Each layer is measured independently and records its own error count. A
    layer where every query errored is reported with ``measured=0`` so the
    verdict can say "could not measure" rather than "scored zero".
    """
    from rugbrain.qdrant_store.store import QdrantVectorStore

    store = QdrantVectorStore()
    k_values = (5, 10)
    per_layer: dict[str, list[dict[str, float]]] = {layer: [] for layer in layers}
    errors: dict[str, int] = {layer: 0 for layer in layers}

    retrievers = {
        "dense": _make_dense_retriever(store, limit),
        "hybrid": _make_hybrid_retriever(store, limit),
    }

    for row in rows:
        if not row.get("query"):
            continue
        collection = row.get("collection") or default_collection
        query = str(row["query"])

        for layer in layers:
            try:
                if layer == "rerank":
                    base = await retrievers["hybrid"](query, collection)
                    hits = await _rerank(query, base, candidate_cap)
                else:
                    hits = await retrievers[layer](query, collection)
            except Exception as exc:  # noqa: BLE001 - one bad query must not abort the run
                errors[layer] += 1
                if errors[layer] == 1:
                    import logging

                    logging.getLogger(__name__).warning(
                        "eval_%s_error collection=%s: %s", layer, collection, exc
                    )
                continue
            if not hits:
                errors[layer] += 1
                continue
            per_layer[layer].append(_score_hits(row, hits, k_values))

    result = Measurement(
        corpus_hash=corpus_hash(rows),
        case_count=len(rows),
        layers={
            layer: LayerMetrics(
                **_average(scores).as_dict(), measured=len(scores), errors=errors[layer]
            )
            for layer, scores in per_layer.items()
        },
        dim_checks=list(dim_checks or []),
    )
    return result


def load_rows(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL golden set into plain dicts.

    Plain dicts, not ``GoldenQuery`` models: the runner must not inherit the
    provenance contract, because provenance is a human judgement and this
    process has no standing to make one. It measures whatever cases exist and
    reports the count.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"golden set not found: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def default_golden_path() -> Path:
    return Path(__file__).resolve().parent / "golden" / "crypto_rag_golden.jsonl"


def default_baseline_path() -> Path:
    return Path(__file__).resolve().parent / "baselines.ratchet.json"


def measured_collections(rows: list[dict[str, Any]], default: str) -> list[str]:
    """The distinct collections this run actually touches, for the preflight."""
    return sorted({str(r.get("collection") or default) for r in rows if r.get("query")}) or [
        default
    ]


def rerank_enabled() -> bool:
    from rugbrain.search import rerank as rerank_mod

    return bool(getattr(rerank_mod, "RERANKER_ENABLED", False))


def qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", "http://localhost:6334")


__all__ = [
    "DEFAULT_CANDIDATE_CAP",
    "DEFAULT_LIMIT",
    "corpus_hash",
    "default_baseline_path",
    "default_golden_path",
    "load_rows",
    "measure",
    "measured_collections",
    "qdrant_url",
    "rerank_enabled",
]
