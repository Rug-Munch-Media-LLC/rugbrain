"""Query-parameter contract for ``GET /api/v1/search``.

The endpoint carries eighteen query parameters, six of which are Qdrant
payload filters. Declaring them as a FastAPI dependency keeps the route
handler a readable pipeline instead of an eighty-line signature, and keeps
this table the single source of truth for the request contract — the handler
never re-describes a filter, it just reads :class:`SearchParams`.

``SearchParams`` is a plain class, not a ``BaseModel``: FastAPI resolves each
field through the ``Query(...)`` defaults declared here, and the handler
receives it via ``Depends(search_params)`` so the OpenAPI document is
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Query

SEARCH_DOC = """Semantic search across scanner findings ingested into Qdrant.

Queries the ``threat_intel_v2`` collection (populated in real-time by the
scanner subscriber) for tokens that match the query by semantic similarity.

When ``rerank=true``, the top-20 candidates from vector search are scored
by the self-hosted Qwen3-Reranker-4B cross-encoder and re-sorted before truncating
to ``limit``. If the reranker backend is unavailable, results fall back
to the original vector ranking (search never breaks).

Crypto-specific payload filters combine with hybrid search — Qdrant
applies them *before* fusion, so filtered results are still dense+BM25
fused. Exact-match filters: ``filter_severity``, ``filter_chain``,
``filter_attack_vector``, ``filter_category``, ``filter_protocol``,
``filter_source_type``. Range filters: ``filter_date_after`` /
``filter_date_before`` (ISO dates, compared on the ``date_ts`` timestamp
payload field) and ``filter_amount_min`` / ``filter_amount_max`` (USD,
compared on ``amount_lost_usd``).

Ground News-style rating filters (applied post-search): ``factuality_filter``
(verified|reported|rumor), ``min_source_tier`` (established|independent),
and ``require_corroboration`` (>= 3 independent sources). When any rating
filter is supplied, results whose rating conflicts are dropped, and the
survivors are reweighted so verified/corroborated articles rank higher.

Example::

    curl "http://localhost:8012/api/v1/search?q=reentrancy&collection=threat_intel_v2&filter_severity=critical&filter_chain=ethereum&filter_date_after=2023-01-01&limit=5&factuality_filter=verified&require_corroboration=1\""""


@dataclass
class SearchParams:
    """Every query parameter ``GET /search`` accepts, in one object."""

    q: str = Query(
        default="", description="Search query — token address, name, or scam description"
    )
    limit: int = Query(default=10, ge=1, le=100)
    collection: str = Query(default="threat_intel_v2", description="Qdrant collection to search")
    rerank: bool = Query(
        default=False,
        description=(
            "Rerank top-20 candidates with the self-hosted Qwen3-Reranker-4B cross-encoder"
        ),
    )
    filter_severity: str | None = Query(
        default=None,
        description="Exact-match payload filter: severity (critical|high|medium|low|info)",
    )
    filter_chain: str | None = Query(
        default=None,
        description="Exact-match payload filter: chain (ethereum, bsc, solana, multi, ...)",
    )
    filter_attack_vector: str | None = Query(
        default=None,
        description="Exact-match payload filter: attack_vector (reentrancy, flash-loan, ...)",
    )
    filter_category: str | None = Query(
        default=None,
        description="Exact-match payload filter: category (smart-contract, phishing, ...)",
    )
    filter_protocol: str | None = Query(
        default=None, description="Exact-match payload filter: affected protocol name"
    )
    filter_source_type: str | None = Query(
        default=None,
        description="Exact-match payload filter: source_type (rekt_news, defihacklabs, ...)",
    )
    filter_date_after: str | None = Query(
        default=None,
        description="Range filter: only points with date >= this ISO date (YYYY-MM-DD)",
    )
    filter_date_before: str | None = Query(
        default=None,
        description="Range filter: only points with date <= this ISO date (YYYY-MM-DD)",
    )
    filter_amount_min: float | None = Query(
        default=None,
        ge=0,
        description="Range filter: only points with amount_lost_usd >= this value",
    )
    filter_amount_max: float | None = Query(
        default=None,
        ge=0,
        description="Range filter: only points with amount_lost_usd <= this value",
    )
    factuality_filter: str | None = Query(
        default=None,
        pattern="^(verified|reported|rumor)$",
        description=(
            "Rating filter: only articles with this factuality rating (verified|reported|rumor)"
        ),
    )
    min_source_tier: str | None = Query(
        default=None,
        pattern="^(established|independent)$",
        description=(
            "Rating filter: only articles with at least this source reputation tier "
            "(established|independent)"
        ),
    )
    require_corroboration: bool = Query(
        default=False,
        description="Rating filter: only articles corroborated by >= 3 independent sources",
    )
    chain_data: bool = Query(
        default=False,
        description=(
            "Fall back to chain-indexer parquet (B2 chain-data) when vector "
            "results are empty or the query asks an on-chain data question "
            "(e.g. 'recent ETH transfers', 'who deployed tokens on Base yesterday'). "
            "Results are tagged with source=chain-data-parquet."
        ),
    )

    def filter_kwargs(self) -> dict[str, Any]:
        """The Qdrant payload-filter half of the request, for retrieval.

        ``collection`` is deliberately absent: :func:`search_retrieval.build_filters`
        already pins it to ``None`` so a caller cannot scope its own filter by
        naming a collection here.
        """
        return {
            "severity": self.filter_severity,
            "chain": self.filter_chain,
            "attack_vector": self.filter_attack_vector,
            "category": self.filter_category,
            "protocol": self.filter_protocol,
            "source_type": self.filter_source_type,
            "date_after": self.filter_date_after,
            "date_before": self.filter_date_before,
            "amount_min": self.filter_amount_min,
            "amount_max": self.filter_amount_max,
        }
