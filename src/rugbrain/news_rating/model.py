"""ArticleRating pydantic model and its payload serialisation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArticleRating(BaseModel):
    """Result of rating one news article on the seven quality axes."""

    url: str = ""
    source: str = ""
    published_at: str | None = None
    axes: dict[str, int] = Field(default_factory=dict)
    composite: int = 0
    tier: str = "poor"
    factuality: str = "rumor"
    source_reputation: str = "unknown"
    corroborating_sources: int = 0
    on_chain_evidence: bool = False
    signals: dict[str, list[str]] = Field(default_factory=dict)
    # Extended fields (Part 3)
    clickbait_score: int = 0
    cross_referenced: int = 0
    on_chain_claims: list[dict[str, Any]] = Field(default_factory=list)
    on_chain_verified: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Rating dict to store on Qdrant metadata / search results.

        Includes the legacy ``ratings.py`` keys plus the 0-100 axes and
        ``quality_score`` (composite) used by ``ratings.quality_multiplier``,
        and the extended cross-referencing/clickbait/on-chain fields.
        """
        return {
            "factuality": self.factuality,
            "source_reputation": self.source_reputation,
            "corroborating_sources": self.corroborating_sources,
            "on_chain_evidence": self.on_chain_evidence,
            "axes": dict(self.axes),
            "composite": self.composite,
            "quality_score": self.composite,
            "tier": self.tier,
            "clickbait_score": self.clickbait_score,
            "cross_referenced": self.cross_referenced,
            "on_chain_claims": list(self.on_chain_claims),
            "on_chain_verified": self.on_chain_verified,
        }
