"""Chain-indexer parquet fallback for search — the storage edge for chain data.

ONE reason this module exists: it is the single place outside ``routes/`` that
reaches ``chain_parquet`` (→ ``parquet_analytics`` → ``duckdb``). Route modules
must not touch storage/clients directly (``.importlinter`` contract
``routes-thin``), so the reach lives here and the routers call
``merge_chain_data()``.

The ``chain_parquet`` import stays FUNCTION-LOCAL. That is deliberate, and NOT
to break an import cycle — there is none: ``chain_parquet`` imports only
``parquet_analytics``, which imports nothing from ``rugbrain`` at all, so the
graph is a leaf and a module-level import is safe (verified: importing
``rugbrain.chain_parquet`` from the routes import position succeeds). The
function-local form is kept for LAZINESS — DuckDB + the parquet stack stay off
the router import path, and a broken/partial parquet install degrades to a
fail-open fallback instead of a 500 on every search.

Fail-open throughout: a fallback failure never breaks the vector search that
already succeeded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_TAG = "chain-data-parquet"
DATA_SOURCE_TAG = "b2:chain-data/parquet"


@dataclass
class ChainDataOutcome:
    """Rows merged in, plus the diagnostics the response reports."""

    results: list[dict[str, Any]]
    applied: bool = False
    intent: bool = False
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def merge_chain_data(
    results: list[dict[str, Any]], query: str, limit: int, enabled: bool
) -> ChainDataOutcome:
    """Return ``results`` plus any chain-data rows, tagged by source."""
    if not enabled:
        return ChainDataOutcome(results)
    try:
        from rugbrain.chain_parquet import (
            build_intent_sql,
            detect_chain_data_intent,
            extract_chain_from_query,
            extract_date_from_query,
            run_chain_query,
        )
    except Exception as exc:
        return ChainDataOutcome(results, error=str(exc)[:200])

    intent = False
    try:
        intent = detect_chain_data_intent(query)
        if results and not intent:
            return ChainDataOutcome(results, intent=False)
        chain = extract_chain_from_query(query)
        date = extract_date_from_query(query)
        sql = build_intent_sql(query, chain=chain, date=date, limit=limit)
        result = run_chain_query(sql, chain=chain, date=date, limit=limit)
        if not result.get("row_count", 0):
            return ChainDataOutcome(results, intent=intent)
        cols = result["columns"]
        rows = [
            {
                **dict(zip(cols, row, strict=False)),
                "source": SOURCE_TAG,
                "data_source": DATA_SOURCE_TAG,
                "chain_data": True,
            }
            for row in result["rows"]
        ]
        return ChainDataOutcome([*results, *rows], applied=True, intent=intent)
    except Exception as exc:
        logger.debug("chain_data_fallback failed (non-critical): %s", exc)
        return ChainDataOutcome(results, intent=intent, error=str(exc)[:200])
