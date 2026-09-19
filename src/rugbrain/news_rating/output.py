"""Output assembly: rate_article and legacy rating-dict normalisation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from rugbrain.news_rating.axes import (
    AXES,
    AXIS_WEIGHTS,
    FACTUALITY_REPORTED_MIN,
    FACTUALITY_VERIFIED_MIN,
    _clamp,
)
from rugbrain.news_rating.bias import bias_score
from rugbrain.news_rating.clickbait import clickbait_score
from rugbrain.news_rating.corroboration import (
    corroboration_score,
    cross_reference_count,
)
from rugbrain.news_rating.diversity import source_diversity_score
from rugbrain.news_rating.factual import _find_claims, factual_density_score
from rugbrain.news_rating.model import ArticleRating
from rugbrain.news_rating.onchain import _score_text, on_chain_claim_verification
from rugbrain.news_rating.sources import (
    source_reputation_label,
    source_reputation_score,
)
from rugbrain.news_rating.technical import technical_depth_score
from rugbrain.news_rating.timeliness import timeliness_score


def _composite_tier(composite: int) -> tuple[str, str]:
    """Map a composite score to (tier, factuality) labels."""
    if composite >= 80:
        tier = "excellent"
    elif composite >= 65:
        tier = "good"
    elif composite >= 50:
        tier = "fair"
    else:
        tier = "poor"

    if composite >= FACTUALITY_VERIFIED_MIN:
        factuality = "verified"
    elif composite >= FACTUALITY_REPORTED_MIN:
        factuality = "reported"
    else:
        factuality = "rumor"
    return tier, factuality


def _weak_signal_map(
    axes: dict[str, float],
    cb: int,
    onchain_verification: dict[str, Any],
) -> dict[str, list[str]]:
    """Collect the per-axis weakness signals for the rating payload."""
    density = axes["factual_density"]
    bias = axes["bias"]
    corroboration = axes["corroboration"]
    depth = axes["technical_depth"]
    diversity = axes["source_diversity"]
    signals: dict[str, list[str]] = {}
    if density < 30:
        signals["factual_density"] = ["few_verifiable_claims"]
    if bias < 50:
        signals["bias"] = ["emotional_or_hype_language"]
    if cb > 25:
        signals["clickbait"] = ["clickbait_headline_or_body"]
    if corroboration <= 30:
        signals["corroboration"] = ["unconfirmed_single_source"]
    if depth < 40:
        signals["technical_depth"] = ["surface_level_only"]
    if diversity <= 40:
        signals["source_diversity"] = ["single_source"]
    if not onchain_verification["verified"] and onchain_verification["claims"]:
        signals["on_chain"] = ["unverified_amount_claim"]
    return signals


def rate_article(article: dict[str, Any], now: datetime | None = None) -> ArticleRating:
    """Rate one article dict on all seven axes and produce a composite.

    Recognised article keys: ``url``, ``source``, ``title``, ``content``
    (or ``body``/``summary``), ``published_at``, ``event_at``,
    ``corroborating_sources``, ``source_reputation_override``,
    ``on_chain_evidence``, ``aggregated``, ``source_urls``/``references``/
    ``related_articles``/``citations``.
    """
    url = str(article.get("url") or "")
    source = str(article.get("source") or "")
    content = str(article.get("content") or article.get("body") or article.get("summary") or "")
    text = _score_text({"title": article.get("title"), "content": content})
    published = article.get("published_at")

    credibility = source_reputation_score(
        source, url, override=article.get("source_reputation_override")
    )
    density = factual_density_score(text)
    fresh = timeliness_score(published, event_at=article.get("event_at"), now=now)

    # Clickbait detection feeds the bias axis: a clickbait-heavy article is
    # penalized on top of the emotional/hype lexicon penalties.
    cb = clickbait_score(text)
    bias = bias_score(text)
    if cb > 0:
        bias = _clamp(bias - (cb // 4))

    # Cross-referencing: corroboration axis computed from trusted sources
    # listed on the article (explicit corroborating_sources wins).
    xref = cross_reference_count(article)
    corroboration = corroboration_score(xref)

    depth = technical_depth_score(text)
    diversity = source_diversity_score(article)

    axes = {
        "source_credibility": credibility,
        "factual_density": density,
        "timeliness": fresh,
        "bias": bias,
        "corroboration": corroboration,
        "technical_depth": depth,
        "source_diversity": diversity,
    }
    composite = round(sum(AXIS_WEIGHTS[axis] * axes[axis] for axis in AXES))
    composite = _clamp(composite)
    tier, factuality = _composite_tier(composite)

    on_chain = bool(article.get("on_chain_evidence")) or (
        len(_find_claims(text)["addresses"] + _find_claims(text)["tx_hashes"]) > 0 and density >= 60
    )
    onchain_verification = on_chain_claim_verification(article)
    signals = _weak_signal_map({k: float(v) for k, v in axes.items()}, cb, onchain_verification)

    return ArticleRating(
        url=url,
        source=source,
        published_at=(published.isoformat() if isinstance(published, (datetime, date)) else None),
        axes=axes,
        composite=composite,
        tier=tier,
        factuality=factuality,
        source_reputation=source_reputation_label(credibility),
        corroborating_sources=max(0, xref),
        on_chain_evidence=on_chain,
        signals=signals,
        clickbait_score=cb,
        cross_referenced=max(0, xref),
        on_chain_claims=onchain_verification["claims"],
        on_chain_verified=onchain_verification["verified"],
    )


def rate_article_from_rating_dict(rating: dict[str, Any]) -> dict[str, Any]:
    """Normalise an already-stored rating payload.

    Returns the payload unchanged when it carries the 0-100 axes, otherwise
    fills missing axes with the legacy fields so downstream consumers always
    see the full seven-axis shape. Used by the API when a Qdrant point only
    holds a legacy rating.
    """
    if isinstance(rating.get("axes"), dict) and rating.get("composite") is not None:
        return dict(rating)
    axis = {
        "source_credibility": source_reputation_score("", rating.get("url", "")),
        "factual_density": 50,
        "timeliness": 50,
        "bias": 50,
        "corroboration": corroboration_score(rating.get("corroborating_sources")),
        "technical_depth": 50,
        "source_diversity": 50,
    }
    return {
        "factuality": rating.get("factuality", "reported"),
        "source_reputation": rating.get("source_reputation", "unknown"),
        "corroborating_sources": rating.get("corroborating_sources", 0),
        "on_chain_evidence": bool(rating.get("on_chain_evidence", False)),
        "axes": axis,
        "composite": int(rating.get("composite", rating.get("quality_score", 50))),
        "quality_score": int(rating.get("composite", rating.get("quality_score", 50))),
        "tier": rating.get("tier", "fair"),
        "clickbait_score": int(rating.get("clickbait_score", 0)),
        "cross_referenced": int(
            rating.get("cross_referenced", rating.get("corroborating_sources", 0))
        ),
        "on_chain_claims": rating.get("on_chain_claims", []),
        "on_chain_verified": bool(
            rating.get("on_chain_verified", rating.get("on_chain_evidence", False))
        ),
    }
