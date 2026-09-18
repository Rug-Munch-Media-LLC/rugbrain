"""Bias detection: FUD/hype lexicon, balanced markers, emotional language."""

from __future__ import annotations

import re

from rugbrain.news_rating.axes import _clamp
from rugbrain.news_rating.clickbait import CLICKBAIT_PHRASES

FUD_WORDS: tuple[str, ...] = (
    "crash",
    "plunge",
    "plummet",
    "collapse",
    "disaster",
    "catastrophe",
    "panic",
    "death",
    "rekt",
    "wrecked",
    "devastating",
    "shocking",
    "bloodbath",
    "annihilation",
    "terrifying",
    "apocalypse",
    "implode",
    "rug pull",
    "scam alert",
    "massive selloff",
)
HYPE_WORDS: tuple[str, ...] = (
    "moon",
    "mooning",
    "lambo",
    "wen lambo",
    "gem",
    "100x",
    "1000x",
    "next big thing",
    "pump",
    "safu",
    "skyrocket",
    "explosive",
    "soar",
    "insane gains",
    "guaranteed",
    "get rich",
    "number go up",
    "fomo",
)
BALANCED_MARKERS: tuple[str, ...] = (
    "however",
    "according to",
    "sources say",
    "allegedly",
    "declined to comment",
    "on the other hand",
    "in response",
    "skeptics argue",
    "analysts say",
    "disputed",
)
_EMOTIONAL_PUNCTUATION_RE = re.compile(r"[!?]{2,}")
_ALLCAPS_RE = re.compile(r"\b[A-Z]{4,}\b")


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def bias_score(text: str) -> int:
    """0-100: neutral reporting scores high.

    Penalties for FUD/hype lexicon hits, clickbait phrases, repeated
    punctuation and ALL-CAPS shouting, scaled per 100 words. Balanced
    markers (hedging, sourcing attributions) reduce the penalty.
    """
    words = len((text or "").split())
    if words < 10:
        return 50
    lowered = (text or "").lower()
    hits = (
        _count_terms(lowered, FUD_WORDS)
        + _count_terms(lowered, HYPE_WORDS)
        + _count_terms(lowered, CLICKBAIT_PHRASES)
        + len(_EMOTIONAL_PUNCTUATION_RE.findall(text or ""))
        + len(_ALLCAPS_RE.findall(text or ""))
    )
    hits_per_100 = hits / words * 100
    penalty = _clamp(hits_per_100 * 35, low=0, high=70)
    if _count_terms(lowered, BALANCED_MARKERS) > 0:
        penalty = max(0, penalty - 10)
    return _clamp(100 - penalty)
