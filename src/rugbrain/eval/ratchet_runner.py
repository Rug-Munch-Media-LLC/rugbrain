"""Retrieval ratchet: measure recall/MRR/nDCG per layer and compare to a baseline.

Orchestration only after the 2026-09-28 split. Metric definitions live in
`ratchet_metrics`; retriever construction in `ratchet_retrievers`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from rugbrain.eval.baseline_ratchet import Layer, LayerMetrics, Measurement
from rugbrain.eval.ratchet_metrics import (
    _average,
    _score_hits,
    corpus_hash,
    recall_from_gains,
)
from rugbrain.eval.ratchet_retrievers import (
    _make_dense_retriever,
    _make_hybrid_retriever,
    _rerank,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 40
DEFAULT_CANDIDATE_CAP = 40

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
            except Exception as exc:
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
            # `_average` returns a LayerMetrics whose as_dict() ALREADY carries
            # measured/errors, so re-passing them as keywords raised
            # "got multiple values for keyword argument 'measured'" and took
            # down the entire run after all the embeddings were already paid
            # for. as_dict() is the complete record; the counts are added
            # explicitly below.
            layer: LayerMetrics(
                **{**_average(scores).as_dict(), "measured": len(scores), "errors": errors[layer]}
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

    Each row's ``collection`` is resolved through ``resolve_collection`` at LOAD
    time, exactly as the serving path resolves it. A golden row naming
    ``defi_exploits`` is asking the eval to measure the collection production
    actually reads, which is the ``_8b`` twin; probing the base name measures a
    2560-wide collection the serving path never touches. Leaving the raw name in
    the row is what let the preflight and the measurement disagree about which
    collections were in play.

    Resolving at load also keeps the corpus hash honest: ``corpus_hash`` folds
    the resolved name, so re-pointing a golden row at its twin is visible as a
    hash change rather than being invisible until a metric moves.
    """
    from rugbrain.qdrant_store.lane_resolver import resolve_collection

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"golden set not found: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if row.get("collection"):
            row["collection"] = resolve_collection(str(row["collection"]))
        rows.append(row)
    return rows


def default_golden_path() -> Path:
    return Path(__file__).resolve().parent / "golden" / "crypto_rag_golden.jsonl"


def default_baseline_path() -> Path:
    return Path(__file__).resolve().parent / "baselines.ratchet.json"


def measured_collections(rows: list[dict[str, Any]], default: str) -> list[str]:
    """The distinct collections this run actually touches, for the preflight.

    ``load_rows`` already resolved every row's collection through
    ``resolve_collection``, so this only has to agree with the row values — the
    resolver is applied here too so the function is correct on its own when
    handed rows built by a test or a caller that did not go through load_rows.
    Resolution is idempotent, so the double application is a no-op.

    This used to read the golden row's `collection` field RAW, so it probed BASE
    names (defi_exploits, 2560-wide) while the serving lane writes the _8b twins
    (defi_exploits_8b, 4096-wide). The preflight then correctly reported a
    dimension split that does not exist in production, and the run exited 4
    before measuring anything.

    That is the worst shape of bug: a safety check that is simultaneously
    correct in isolation and wrong in context, so it trains you to ignore it.
    Any component that decides WHICH collections are in play has to agree with
    the component that decides WHICH collections are WRITTEN.
    """
    from rugbrain.qdrant_store.lane_resolver import resolve_collection

    names = {str(r.get("collection") or default) for r in rows if r.get("query")}
    return sorted({resolve_collection(n) for n in names}) or [resolve_collection(default)]


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
    "recall_from_gains",
    "rerank_enabled",
]
