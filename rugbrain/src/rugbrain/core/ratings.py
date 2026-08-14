"""Ground News-style multi-axis article ratings for crypto news.

Each article gets a rating card with:

- Factuality: VERIFIED / REPORTED / OPINION / RUMOR
- Source Reputation: ESTABLISHED / INDEPENDENT / UNKNOWN
- Market Impact: HIGH / MEDIUM / LOW
- Entity Coverage: tokens, protocols, people mentioned
- Sentiment: BULLISH / NEUTRAL / BEARISH
- Timeliness: BREAKING / RECENT / ARCHIVED

The classifier is deliberately dependency-free and deterministic: it uses
curated vocabularies (shared with ``app.rag.dedup`` for entity extraction),
language-pattern counts for factuality, and heuristic marker lists for
market impact / sentiment. It never blocks ingestion — ``rate_article`` is
pure and fail-open (worst case it produces a conservative default card).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from rugbrain._logging import get_logger
from rugbrain.core.dedup import KNOWN_PEOPLE, KNOWN_PROTOCOLS, KNOWN_TOKENS

log = get_logger(__name__)


class Factuality(StrEnum):
    """How certain we are that the article describes a real, verified event."""

    VERIFIED = "verified"  # Confirmed by multiple sources or on-chain
    REPORTED = "reported"  # Single source reporting
    OPINION = "opinion"  # Analysis/editorial
    RUMOR = "rumor"  # Unverified claim, social media


class SourceReputation(StrEnum):
    """Reputation tier of the publishing source."""

    ESTABLISHED = "established"  # CoinDesk, CoinTelegraph, TheBlock
    INDEPENDENT = "independent"  # Medium blogs, individual analysts
    UNKNOWN = "unknown"  # New or unverified source


class MarketImpact(StrEnum):
    """How likely the article is to move markets."""

    HIGH = "high"  # Could move markets
    MEDIUM = "medium"  # Notable but not market-moving
    LOW = "low"  # Background noise


@dataclass
class ArticleRating:
    """Ground News-style rating card for a crypto news article."""

    article_id: str
    title: str
    source: str

    # Multi-axis ratings
    factuality: Factuality = Factuality.REPORTED
    source_reputation: SourceReputation = SourceReputation.UNKNOWN
    market_impact: MarketImpact = MarketImpact.LOW
    sentiment: str = "NEUTRAL"  # BULLISH/BEARISH/NEUTRAL

    # Entity coverage
    tokens_mentioned: list[str] = field(default_factory=list)
    protocols_mentioned: list[str] = field(default_factory=list)
    people_mentioned: list[str] = field(default_factory=list)

    # Timeliness
    published_at: str = ""
    timeliness: str = "RECENT"  # BREAKING (<1h) / RECENT (<24h) / ARCHIVED

    # Confidence
    confidence: float = 0.5  # 0-1, how confident are we in this rating?

    # Evidence
    corroborating_sources: int = 0  # How many other sources report same story?
    on_chain_evidence: bool = False  # Is there on-chain data backing this?

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (enums serialised to their values)."""
        return {
            "article_id": self.article_id,
            "title": self.title,
            "source": self.source,
            "factuality": self.factuality.value,
            "source_reputation": self.source_reputation.value,
            "market_impact": self.market_impact.value,
            "sentiment": self.sentiment,
            "tokens_mentioned": list(self.tokens_mentioned),
            "protocols_mentioned": list(self.protocols_mentioned),
            "people_mentioned": list(self.people_mentioned),
            "published_at": self.published_at,
            "timeliness": self.timeliness,
            "confidence": self.confidence,
            "corroborating_sources": self.corroborating_sources,
            "on_chain_evidence": self.on_chain_evidence,
        }


# ── Source reputation ────────────────────────────────────────────────────

# Domain (bare, lowercase, no scheme/www/path) → reputation tier.
# Extends the pre-loaded map in the spec with the feed domains from
# ``crawl_sources.json`` plus common mainstream outlets so the rating
# works out-of-the-box for the actual ingestion pipeline.
SOURCE_TIER_MAP: dict[str, str] = {
    # Spec-provided crypto + mainstream outlets.
    "coindesk.com": "established",
    "cointelegraph.com": "established",
    "theblock.co": "established",
    "decrypt.co": "established",
    "reuters.com": "established",
    "bloomberg.com": "established",
    # Other established crypto media (crawl_sources.json feeds).
    "cryptoslate.com": "established",
    "cryptobriefing.com": "established",
    "beincrypto.com": "established",
    "news.bitcoin.com": "established",
    "dailyhodl.com": "established",
    "ambcrypto.com": "established",
    "blockworks.co": "established",
    "thedefiant.io": "established",
    # Mainstream business / financial press.
    "cnbc.com": "established",
    "forbes.com": "established",
    "fortune.com": "established",
    "wsj.com": "established",
    "nytimes.com": "established",
    "ft.com": "established",
    "apnews.com": "established",
    "axios.com": "established",
    "businessinsider.com": "established",
    "yahoo.com": "established",
    # Established research / security / official orgs.
    "messari.io": "established",
    "chainalysis.com": "established",
    "openzeppelin.com": "established",
    "trailofbits.com": "established",
    "solana.com": "established",
    # Blog platforms / org blogs / individual analysts.
    "medium.com": "independent",
    "immunefi.com": "independent",
    "dune.com": "independent",
    "slowmist.com": "independent",
    "peckshield.com": "independent",
    "substack.com": "independent",
    "mirror.xyz": "independent",
    "paragraph.xyz": "independent",
}

_CASHTAG_RE = re.compile(r"\$([a-zA-Z]{2,12})\b")


def _normalise_source(source: str) -> str:
    """Reduce a source URL or name to a bare lowercase domain."""
    if not source:
        return ""
    s = source.strip().lower()
    if "://" in s or s.startswith("www."):
        with contextlib.suppress(ValueError):
            s = urlparse(s if "://" in s else f"http://{s}").hostname or s
    s = s.strip().lower()
    if s.startswith("www."):
        s = s[4:]
    # Strip trailing path segments (e.g. "cointelegraph.com/rss" → domain).
    if "." in s and "/" in s:
        s = s.split("/", 1)[0]
    return s


def _source_tier(source: str, source_tier_map: dict[str, str] | None = None) -> SourceReputation:
    """Look up the reputation tier for *source*.

    Bare-domain match against ``SOURCE_TIER_MAP`` (or an override map).
    An override map takes precedence per-domain and falls back to the
    built-in map for domains it does not mention. Unknown sources fall
    back to ``UNKNOWN`` — never raises.
    """
    domain = _normalise_source(source)
    if not domain:
        return SourceReputation.UNKNOWN
    tier = None
    if source_tier_map is not None:
        tier = source_tier_map.get(domain)
        if tier is None:
            # Accept an override keyed by full source name ("CoinDesk").
            tier = source_tier_map.get(source.strip().lower())
    if tier is None:
        tier = SOURCE_TIER_MAP.get(domain)
        if tier is None:
            # Also accept a default-map key by full source name.
            tier = SOURCE_TIER_MAP.get(source.strip().lower())
    if tier == "established":
        return SourceReputation.ESTABLISHED
    if tier == "independent":
        return SourceReputation.INDEPENDENT
    return SourceReputation.UNKNOWN


# ── Factuality classification ────────────────────────────────────────────

VERIFIED_INDICATORS = ["confirmed", "announced", "published", "reported by", "according to", "stated"]
RUMOR_INDICATORS = ["rumor", "speculation", "unnamed source", "allegedly", "could", "might", "potential"]
OPINION_INDICATORS = ["opinion", "analysis", "why", "how", "what if", "could mean", "might indicate"]

# Strong signals that override counting — a single hit is decisive.
_STRONG_RUMOR_WORDS = ("rumor", "speculation", "unnamed source", "allegedly")
_STRONG_OPINION_WORDS = ("opinion", "analysis", "editorial", "commentary")


def _count_indicators(text: str, indicators: list[str]) -> int:
    """Count how many distinct indicator phrases appear in *text*."""
    if not text:
        return 0
    lower = text.lower()
    return sum(1 for phrase in indicators if phrase in lower)


def _classify_factuality(text: str, tier: SourceReputation) -> Factuality:
    """Classify factuality from language patterns + source reputation tier."""
    lower = (text or "").lower()

    # Strong rumor signals always win — even established outlets report rumors.
    if any(word in lower for word in _STRONG_RUMOR_WORDS):
        return Factuality.RUMOR

    # Strong opinion signals.
    if any(word in lower for word in _STRONG_OPINION_WORDS):
        return Factuality.OPINION

    verified = _count_indicators(lower, VERIFIED_INDICATORS)
    rumor = _count_indicators(lower, RUMOR_INDICATORS)
    opinion = _count_indicators(lower, OPINION_INDICATORS)

    # Multiple hedge words ("could", "might", "potential") → rumor territory.
    if rumor >= 2:
        return Factuality.RUMOR

    # Explicit analysis framing.
    if opinion >= 2:
        return Factuality.OPINION

    # Confirmation language only counts as VERIFIED when the source is
    # established (single independent blogs can't verify on their own).
    if verified >= 1 and tier == SourceReputation.ESTABLISHED:
        return Factuality.VERIFIED

    # A single "could/might" hedge or single "why/how" framing.
    if rumor >= 1 or opinion >= 1:
        return Factuality.REPORTED

    # Confirmation language from a non-established source → single-source
    # reporting, not yet verified.
    if verified >= 1:
        return Factuality.REPORTED

    return Factuality.REPORTED


def classify_factuality(text: str, source: str) -> Factuality:
    """Classify based on language patterns and source reputation."""
    tier = _source_tier(source)
    return _classify_factuality(text, tier)


# ── Market impact assessment ─────────────────────────────────────────────

_AGENCY_WORDS = ("sec", "cftc", "doj", "fincen", "occ", "mica")
_SECURITY_WORDS = ("hack", "hacked", "hacking", "exploit", "drained", "stolen", "breach", "theft", "attack")
_LISTING_RE = re.compile(r"\b(listing|delist|delisting|delisted|listed on)\b", re.IGNORECASE)
_MAJOR_UPGRADE_RE = re.compile(
    r"\b(mainnet|halving|hard fork|genesis)\b"
    r"|\b(major|significant|biggest|massive)\s+(protocol\s+|network\s+)?upgrade\b",
    re.IGNORECASE,
)
_MINOR_UPGRADE_RE = re.compile(r"\b(upgrade|fork|proposal)\b", re.IGNORECASE)
_MEDIUM_IMPACT_WORDS = (
    "partnership",
    "partner with",
    "funding",
    "raises",
    "raise",
    "seed round",
    "series a",
    "series b",
    "regulatory",
    "regulation",
    "lawsuit",
    "settlement",
    "integration",
    "acquires",
    "acquisition",
)
_AMOUNT_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*([kmb])\b", re.IGNORECASE)
_AMOUNT_MULTIPLIERS = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}
_HACK_AMOUNT_THRESHOLD = 10_000_000.0  # $10M stolen → HIGH impact


def _extract_amount(text: str) -> float | None:
    """Largest dollar amount (with k/m/b suffix) mentioned, or None."""
    best: float | None = None
    for match in _AMOUNT_RE.finditer(text):
        try:
            value = float(match.group(1)) * _AMOUNT_MULTIPLIERS[match.group(2).lower()]
        except (ValueError, KeyError):
            continue
        if best is None or value > best:
            best = value
    return best


def assess_market_impact(text: str, entities: list[str]) -> MarketImpact:
    """Assess potential market impact based on content and entities."""
    if not text:
        return MarketImpact.LOW
    lower = text.lower()

    # HIGH: regulatory agency / ETF news.
    if any(agency in lower for agency in _AGENCY_WORDS) or re.search(r"\betf\b", lower):
        return MarketImpact.HIGH

    # HIGH: CEX listing / delisting.
    if _LISTING_RE.search(lower):
        return MarketImpact.HIGH

    # HIGH: major protocol upgrade, mainnet, halving.
    if _MAJOR_UPGRADE_RE.search(lower):
        return MarketImpact.HIGH

    # Security incident: amount decides HIGH (> $10M) vs MEDIUM.
    if any(word in lower for word in _SECURITY_WORDS):
        amount = _extract_amount(lower)
        if amount is not None and amount >= _HACK_AMOUNT_THRESHOLD:
            return MarketImpact.HIGH
        return MarketImpact.MEDIUM

    # MEDIUM: minor upgrade, partnership, funding, regulatory comment.
    if _MINOR_UPGRADE_RE.search(lower) or any(word in lower for word in _MEDIUM_IMPACT_WORDS):
        return MarketImpact.MEDIUM

    # LOW: general commentary, price predictions, NFT drops, background.
    return MarketImpact.LOW


# ── Sentiment ────────────────────────────────────────────────────────────

_BULLISH_WORDS = (
    "surge",
    "soar",
    "rally",
    "bullish",
    "all-time high",
    "record high",
    "ath",
    "gains",
    "approval",
    "adoption",
    "inflows",
    "accumulation",
    "breakout",
    "upside",
    "outperform",
)
_BEARISH_WORDS = (
    "crash",
    "plummet",
    "tumble",
    "bearish",
    "sell-off",
    "selloff",
    "dump",
    "dumps",
    "liquidation",
    "outflows",
    "hack",
    "exploit",
    "fraud",
    "scam",
    "rug pull",
    "ban",
    "banned",
    "lawsuit",
    "indictment",
    "crackdown",
    "downgrade",
    "warning",
    "drop",
    "falls",
)


def _count_word_matches(text: str, words: tuple[str, ...]) -> int:
    """Count whole-word (or phrase) matches of *words* in *text*."""
    if not text:
        return 0
    lower = text.lower()
    total = 0
    for word in words:
        if re.search(rf"\b{re.escape(word)}", lower):
            total += 1
    return total


def classify_sentiment(text: str) -> str:
    """Classify article sentiment: BULLISH / BEARISH / NEUTRAL.

    Counts bullish vs bearish lexicon matches; ties resolve to NEUTRAL.
    """
    bullish = _count_word_matches(text, _BULLISH_WORDS)
    bearish = _count_word_matches(text, _BEARISH_WORDS)
    if bullish > bearish:
        return "BULLISH"
    if bearish > bullish:
        return "BEARISH"
    return "NEUTRAL"


# ── Timeliness ───────────────────────────────────────────────────────────

BREAKING_HOURS = 1.0  # <1h
RECENT_HOURS = 24.0  # <24h


def _parse_published_at(value: str) -> datetime | None:
    """Parse an RSS/ISO publish timestamp to a tz-aware UTC datetime."""
    if not value:
        return None
    v = value.strip()
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        try:
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def classify_timeliness(published_at: str, *, now: datetime | None = None) -> str:
    """Classify article age: BREAKING (<1h) / RECENT (<24h) / ARCHIVED.

    Unknown publish dates fall back to ``RECENT`` (fail-open — never
    claim BREAKING or ARCHIVED without a datable timestamp).
    """
    published = _parse_published_at(published_at)
    if published is None:
        return "RECENT"
    reference = now or datetime.now(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    if age_hours < BREAKING_HOURS:
        return "BREAKING"
    if age_hours < RECENT_HOURS:
        return "RECENT"
    return "ARCHIVED"


# ── Entity extraction ────────────────────────────────────────────────────

# Reuses the curated vocabularies from ``app.rag.dedup`` (KNOWN_TOKENS,
# KNOWN_PROTOCOLS, KNOWN_PEOPLE) so entity extraction stays consistent
# between dedup and rating.


def extract_entities(text: str) -> dict[str, list[str]]:
    """Extract tokens, protocols, and people from article text.

    Returns ``{"tokens": [...], "protocols": [...], "people": [...]}`` —
    ordered, de-duplicated, lowercased lists. A word may legitimately
    appear in multiple categories (e.g. UNI the token and Uniswap the
    protocol) so membership is per-category, not exclusive.
    """
    tokens: list[str] = []
    protocols: list[str] = []
    people: list[str] = []
    if not text:
        return {"tokens": tokens, "protocols": protocols, "people": people}

    lowered = text.lower()

    for match in _CASHTAG_RE.finditer(text):
        ticker = match.group(1).lower()
        if ticker not in tokens:
            tokens.append(ticker)

    for token in KNOWN_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", lowered) and token not in tokens:
            tokens.append(token)

    for protocol in KNOWN_PROTOCOLS:
        if re.search(rf"\b{re.escape(protocol)}\b", lowered) and protocol not in protocols:
            protocols.append(protocol)

    for person in KNOWN_PEOPLE:
        if re.search(rf"\b{re.escape(person)}\b", lowered) and person not in people:
            people.append(person)

    return {"tokens": tokens, "protocols": protocols, "people": people}


# ── Confidence ───────────────────────────────────────────────────────────


def compute_rating_confidence(rating: ArticleRating) -> float:
    """How confident are we in this rating? Based on source tier, corroboration, etc.

    Composition (all additive, clamped to 0-1):
      base 0.3
      + source tier (established 0.3 / independent 0.1)
      + corroborating sources (0.1 each, capped at 0.3)
      + on-chain evidence (0.15)
      + factuality (VERIFIED +0.1, RUMOR -0.1)
    """
    score = 0.3
    if rating.source_reputation == SourceReputation.ESTABLISHED:
        score += 0.3
    elif rating.source_reputation == SourceReputation.INDEPENDENT:
        score += 0.1

    score += min(max(rating.corroborating_sources, 0) * 0.1, 0.3)
    if rating.on_chain_evidence:
        score += 0.15

    if rating.factuality == Factuality.VERIFIED:
        score += 0.1
    elif rating.factuality == Factuality.RUMOR:
        score -= 0.1

    return round(min(max(score, 0.0), 1.0), 3)


# ── Orchestrator ─────────────────────────────────────────────────────────


def _article_text(article: dict[str, Any]) -> str:
    """Concatenate the text fields used for classification."""
    parts = (article.get("title"), article.get("summary"), article.get("content"))
    return " ".join(str(p) for p in parts if p)


def _resolve_source(article: dict[str, Any]) -> str:
    """Best-effort source identifier: ``source`` → ``source_url`` → ``url``."""
    for key in ("source", "source_url", "url"):
        value = article.get(key)
        if value:
            return str(value)
    return ""


def rate_article(article: dict[str, Any], source_tier_map: dict[str, str] | None = None) -> ArticleRating:
    """Rate a news article across all axes.

    Args:
        article: Article dict with any of ``title``, ``summary``,
            ``content``, ``source``, ``source_url``, ``url``, ``published``,
            ``published_at``, ``corroborating_sources``,
            ``on_chain_evidence``.
        source_tier_map: Optional domain → tier override map. Defaults to
            ``SOURCE_TIER_MAP``.

    Returns:
        A populated ``ArticleRating``. Never raises for malformed input —
        missing fields fall back to conservative defaults.
    """
    article_id = str(article.get("article_id") or article.get("doc_id") or article.get("url") or "")
    title = str(article.get("title") or "")[:500]
    source = _resolve_source(article)
    text = _article_text(article)

    entities = extract_entities(text)
    all_entities = entities["tokens"] + entities["protocols"] + entities["people"]

    published_at = str(article.get("published") or article.get("published_at") or "")
    tier = _source_tier(source, source_tier_map)

    rating = ArticleRating(
        article_id=article_id,
        title=title,
        source=source,
        factuality=_classify_factuality(text, tier),
        source_reputation=tier,
        market_impact=assess_market_impact(text, all_entities),
        sentiment=classify_sentiment(text),
        tokens_mentioned=entities["tokens"],
        protocols_mentioned=entities["protocols"],
        people_mentioned=entities["people"],
        published_at=published_at,
        timeliness=classify_timeliness(published_at),
        corroborating_sources=max(int(article.get("corroborating_sources") or 0), 0),
        on_chain_evidence=bool(article.get("on_chain_evidence") or False),
    )
    rating.confidence = compute_rating_confidence(rating)
    return rating


__all__ = [
    "OPINION_INDICATORS",
    "RUMOR_INDICATORS",
    "SOURCE_TIER_MAP",
    "VERIFIED_INDICATORS",
    "ArticleRating",
    "Factuality",
    "MarketImpact",
    "SourceReputation",
    "assess_market_impact",
    "classify_factuality",
    "classify_sentiment",
    "classify_timeliness",
    "compute_rating_confidence",
    "extract_entities",
    "rate_article",
]
