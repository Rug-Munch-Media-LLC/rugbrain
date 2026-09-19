"""Ground News-style multi-axis article rating engine for RugBrain.

Rates a news article on seven 0-100 axes and blends them into a single
0-100 composite quality score that powers rugmunch.io's news ranking:

  * ``source_credibility`` — reputation of the publishing source
  * ``factual_density``    — share of verifiable claims (addresses, tx
                             hashes, amounts, protocol names) in the text
  * ``timeliness``         — how fresh the report is vs. when it happened
  * ``bias``               — neutral reporting scores high; FUD/hype and
                             emotional language lower the score
  * ``corroboration``      — how many independent sources report the event
  * ``technical_depth``    — does the article explain blockchain mechanics?
  * ``source_diversity``   — single-source vs. multi-source/aggregated

The engine is pure Python (stdlib + pydantic): deterministic, no network,
safe to run inside the ingestion script and the API process. The produced
``ArticleRating.to_payload()`` dict also emits the legacy ``ratings.py``
keys (``factuality``, ``source_reputation``, ``corroborating_sources``,
``on_chain_evidence``) so the existing search-boost machinery consumes the
new quality score with no changes to its callers, plus ``quality_score``
which drives ``ratings.quality_multiplier``.

Usage::

    rating = rate_article({"url": "https://coindesk.com/...", "content": "..."})
    print(rating.composite, rating.tier)

This package is a behaviour-identical split of the former single-module
``rugbrain.news_rating`` (1078 lines). The public API is re-exported here
so ``from rugbrain.news_rating import rate_article`` keeps working, and
internal callers resolve through this package namespace at call time so
tests that patch module attributes keep working.
"""

from __future__ import annotations

# Re-exported for namespace fidelity with the former single-module
# ``rugbrain.news_rating``: tests and callers reach private helpers and
# constants through this package object (e.g. ``nr._find_claims``,
# ``nr.SOURCE_REPUTATION``), so the imports are intentional.
from rugbrain.news_rating.axes import (  # noqa: F401
    AXES,
    AXIS_WEIGHTS,
    FACTUALITY_REPORTED_MIN,
    FACTUALITY_VERIFIED_MIN,
    REPUTATION_ESTABLISHED_MIN,
    REPUTATION_INDEPENDENT_MIN,
    _clamp,
)
from rugbrain.news_rating.bias import (  # noqa: F401
    BALANCED_MARKERS,
    FUD_WORDS,
    HYPE_WORDS,
    _count_terms,
    bias_score,
)
from rugbrain.news_rating.clickbait import CLICKBAIT_PHRASES, clickbait_score  # noqa: F401
from rugbrain.news_rating.corroboration import (
    corroboration_score,
    cross_reference_count,
    cross_reference_score,
)
from rugbrain.news_rating.diversity import source_diversity_score
from rugbrain.news_rating.factual import (  # noqa: F401
    PROTOCOL_NAMES,
    _find_claims as _find_claims,
    factual_density_score,
)
from rugbrain.news_rating.model import ArticleRating
from rugbrain.news_rating.onchain import on_chain_claim_verification
from rugbrain.news_rating.output import (
    rate_article,
    rate_article_from_rating_dict,
)
from rugbrain.news_rating.sources import (  # noqa: F401
    DEFAULT_SOURCE_REPUTATION,
    SOURCE_NAME_ALIASES,
    SOURCE_REPUTATION,
    domain_from_url,
    source_reputation_label,
    source_reputation_score,
)
from rugbrain.news_rating.technical import TECH_TERMS, technical_depth_score  # noqa: F401
from rugbrain.news_rating.timeliness import timeliness_score

__all__ = [
    "AXES",
    "AXIS_WEIGHTS",
    "SOURCE_REPUTATION",
    "ArticleRating",
    "bias_score",
    "clickbait_score",
    "corroboration_score",
    "cross_reference_count",
    "cross_reference_score",
    "domain_from_url",
    "factual_density_score",
    "on_chain_claim_verification",
    "rate_article",
    "rate_article_from_rating_dict",
    "source_diversity_score",
    "source_reputation_label",
    "source_reputation_score",
    "technical_depth_score",
    "timeliness_score",
]
