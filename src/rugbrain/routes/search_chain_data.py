"""Thin route-local re-export of the chain-data fallback service.

The storage reach (chain_parquet → parquet_analytics → duckdb) now lives in
``rugbrain.chain_data_service``, which is outside ``routes/``. Keeping this
module means the existing import site
(``routes.search_pkg.threat`` → ``from rugbrain.routes.search_chain_data import
merge_chain_data``) and its response envelope are unchanged; it holds no
storage import of its own.

This is a RE-EXPORT, not a copy — the dataclass identity must be shared:
``ChainDataOutcome`` returned here is the same class the service constructs.
"""

from __future__ import annotations

from rugbrain.chain_data_service import (
    DATA_SOURCE_TAG,
    SOURCE_TAG,
    ChainDataOutcome,
    merge_chain_data,
)

__all__ = [
    "DATA_SOURCE_TAG",
    "SOURCE_TAG",
    "ChainDataOutcome",
    "merge_chain_data",
]
