"""Chain-indexer parquet fallback for search.

One job: when the caller opts in and either vector search returned nothing or
the query is clearly an on-chain data question, merge rows read from the
indexer's B2 chain-data parquet. Fail-open — a fallback failure never breaks
the vector search that already succeeded.
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
            {**dict(zip(cols, row, strict=False)),
             "source": SOURCE_TAG,
             "data_source": DATA_SOURCE_TAG,
             "chain_data": True}
            for row in result["rows"]
        ]
        return ChainDataOutcome([*results, *rows], applied=True, intent=intent)
    except Exception as exc:
        logger.debug("chain_data_fallback failed (non-critical): %s", exc)
        return ChainDataOutcome(results, intent=intent, error=str(exc)[:200])
