"""Clickbait detection: phrases, ALL-CAPS shouting, punctuation, emoji."""

from __future__ import annotations

import re

from rugbrain.news_rating.axes import _clamp

_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "]+"
)

CLICKBAIT_PHRASES: tuple[str, ...] = (
    "you won't believe",
    "you wont believe",
    "this changes everything",
    "insiders reveal",
    "don't miss out",
    "dont miss out",
    "secret revealed",
    "shocking truth",
    "to the moon",
    "buy now",
    "number go up",
    "wen lambo",
    "100x gem",
    "1000x",
    "the truth about",
    "what happens next",
    "insane gains",
    "guaranteed profit",
    "act fast",
    "limited time",
    "last chance",
    "click here",
    "this one trick",
    "shocked everyone",
    "mind blowing",
    "going to explode",
    "x100",
    "x1000",
    "never sell",
    "get rich",
)

_EMOTIONAL_PUNCTUATION_RE = re.compile(r"[!?]{2,}")
_ALLCAPS_RE = re.compile(r"\b[A-Z]{4,}\b")


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def clickbait_score(text: str) -> int:
    """0-100: how clickbait-y the headline/body is.

    Detects classic clickbait phrases, ALL-CAPS shouting (ratio of
    ALL-CAPS words), excessive exclamation/question punctuation, and
    emoji density. Higher score = more clickbait.
    """
    if not (text or "").strip():
        return 0
    words = len(text.split())
    if words < 5:
        return 0
    lowered = text.lower()

    phrase_hits = _count_terms(lowered, CLICKBAIT_PHRASES)
    punct_hits = len(_EMOTIONAL_PUNCTUATION_RE.findall(text))
    emoji_hits = len(_EMOJI_RE.findall(text))

    allcaps_words = len(_ALLCAPS_RE.findall(text))
    allcaps_ratio = allcaps_words / words

    score = 0.0
    score += phrase_hits * 22
    score += min(punct_hits * 8, 24)
    score += min(emoji_hits * 6, 18)
    if allcaps_ratio > 0.05:
        score += min(allcaps_ratio * 120, 30)
    return _clamp(score)
