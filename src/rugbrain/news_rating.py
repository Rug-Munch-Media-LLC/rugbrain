"""
Ground News-style multi-axis article rating engine for RugBrain.

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
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Axis registry ───────────────────────────────────────────────────────
AXES: tuple[str, ...] = (
    "source_credibility",
    "factual_density",
    "timeliness",
    "bias",
    "corroboration",
    "technical_depth",
    "source_diversity",
)

AXIS_WEIGHTS: dict[str, float] = {
    "source_credibility": 0.25,
    "factual_density": 0.15,
    "timeliness": 0.10,
    "bias": 0.15,
    "corroboration": 0.15,
    "technical_depth": 0.10,
    "source_diversity": 0.10,
}

# Legacy factuality/source-reputation thresholds (bridge to ratings.py).
FACTUALITY_VERIFIED_MIN = 75
FACTUALITY_REPORTED_MIN = 50
REPUTATION_ESTABLISHED_MIN = 75
REPUTATION_INDEPENDENT_MIN = 50


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


# ═══════════════════════════════════════════════════════════════════════
# Source credibility
# ═══════════════════════════════════════════════════════════════════════

#: domain → (0-100 reputation, tier label). Unknown domains default low.
# The expanded 100+ source database lives in ``news_sources`` and is merged
# here so the single ``SOURCE_REPUTATION`` dict remains the lookup surface.
try:
    from rugbrain.news_sources import merged_source_reputation

    SOURCE_REPUTATION: dict[str, tuple[int, str]] = merged_source_reputation()
except ImportError:  # pragma: no cover — fallback keeps the module importable
    SOURCE_REPUTATION = {
        "reuters.com": (96, "established"),
        "bloomberg.com": (96, "established"),
        "apnews.com": (96, "established"),
        "coindesk.com": (95, "established"),
        "ft.com": (95, "established"),
        "wsj.com": (95, "established"),
        "nytimes.com": (95, "established"),
        "theblock.co": (94, "established"),
        "theguardian.com": (92, "established"),
        "decrypt.co": (92, "established"),
        "cointelegraph.com": (90, "established"),
        "blockworks.co": (90, "established"),
        "thedefiant.io": (88, "established"),
        "axios.com": (85, "established"),
        "forbes.com": (85, "established"),
        "bitcoinmagazine.com": (80, "established"),
        "beincrypto.com": (72, "independent"),
        "cryptonews.com": (70, "independent"),
        "news.bitcoin.com": (70, "independent"),
        "ambcrypto.com": (70, "independent"),
        "u.today": (68, "independent"),
        "dailyhodl.com": (66, "independent"),
        "zycrypto.com": (64, "independent"),
        "coinjournal.net": (62, "independent"),
    }

#: source *name* aliases → domain (fallback when the URL is absent).
SOURCE_NAME_ALIASES: dict[str, str] = {
    "coindesk": "coindesk.com",
    "the block": "theblock.co",
    "decrypt": "decrypt.co",
    "cointelegraph": "cointelegraph.com",
    "blockworks": "blockworks.co",
    "the defiant": "thedefiant.io",
    "reuters": "reuters.com",
    "bloomberg": "bloomberg.com",
    "associated press": "apnews.com",
    "forbes": "forbes.com",
    "the guardian": "theguardian.com",
    "financial times": "ft.com",
    "wall street journal": "wsj.com",
    "new york times": "nytimes.com",
    "axios": "axios.com",
    "bitcoin magazine": "bitcoinmagazine.com",
    "beincrypto": "beincrypto.com",
    "cryptonews": "cryptonews.com",
    "bitcoin.com news": "news.bitcoin.com",
    "u.today": "u.today",
    "daily hodl": "dailyhodl.com",
    "zycrypto": "zycrypto.com",
    "coinjournal": "coinjournal.net",
    "ambcrypto": "ambcrypto.com",
}
try:  # merge the expanded alias table (extended wins on conflict)
    from rugbrain.news_sources import EXTENDED_SOURCE_ALIASES

    SOURCE_NAME_ALIASES.update(EXTENDED_SOURCE_ALIASES)
except ImportError:  # pragma: no cover
    pass

DEFAULT_SOURCE_REPUTATION = 45


def domain_from_url(url: str) -> str:
    """Extract a lowercase bare domain (no scheme, port, or path) from *url*."""
    if not url:
        return ""
    match = re.match(r"(?:https?://)?(?:www\.)?([^/:?#]+)", url.strip())
    if not match:
        return ""
    return match.group(1).lower()


def source_reputation_score(source: str, url: str = "", override: int | None = None) -> int:
    """0-100 reputation score for a source name/domain.

    Priority: explicit ``override`` > domain lookup > source-name alias
    lookup > default (unknown blog).
    """
    if override is not None:
        return _clamp(override)
    domain = domain_from_url(url)
    if domain in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[domain][0]
    name = (source or "").strip().lower()
    if name in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[name][0]
    alias = SOURCE_NAME_ALIASES.get(name)
    if alias and alias in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[alias][0]
    return DEFAULT_SOURCE_REPUTATION


def source_reputation_label(score: int) -> str:
    """Tier label for a source reputation score."""
    if score >= REPUTATION_ESTABLISHED_MIN:
        return "established"
    if score >= REPUTATION_INDEPENDENT_MIN:
        return "independent"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Factual density
# ═══════════════════════════════════════════════════════════════════════

_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
_SOLANA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_AMOUNT_USD_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?[kmb]?\b", re.IGNORECASE)
_AMOUNT_TOKEN_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:ETH|BTC|SOL|USDT|USDC|BNB|XRP|ADA|DOGE|AVAX|MATIC|LINK|DOT|UNI|DAI|ARB|OP|TON|TRX|LTC|BCH|ATOM|NEAR|APT|SUI)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")

PROTOCOL_NAMES: tuple[str, ...] = (
    "uniswap",
    "aave",
    "compound",
    "curve",
    "lido",
    "makerdao",
    "gmx",
    "jupiter",
    "raydium",
    "marinade",
    "jito",
    "metaplex",
    "opensea",
    "blur",
    "balancer",
    "sushiswap",
    "pancakeswap",
    "1inch",
    "dydx",
    "synthetix",
    "yearn",
    "convex",
    "rocket pool",
    "eigenlayer",
    "arbitrum",
    "optimism",
    "base",
    "polygon",
    "avalanche",
    "solana",
    "ethereum",
    "bitcoin",
    "tether",
    "chainlink",
    "pyth",
    "wormhole",
    "layerzero",
    "hyperliquid",
    "tornado cash",
    "euler",
    "mango",
    "platypus",
    "radiant",
    "venus",
    "benqi",
    "cream",
    "iron bank",
    "rari",
    "fei",
    "olympus",
    "terra",
    "luna",
    "ftt",
    "alameda",
    "ftx",
    "celsius",
    "blockfi",
    "voyager",
    "genesis",
    "harmony",
    "ronin",
    "axie",
    "nomad",
    "wintermute",
    "liquidity",
    "collateral",
    "multisig",
    "smart contract",
    "tokenomics",
)
_PROTOCOL_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in sorted(PROTOCOL_NAMES, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _find_claims(text: str) -> dict[str, list[str]]:
    """Return verifiable claim categories found in *text* (deduplicated)."""
    claims: dict[str, list[str]] = {
        "addresses": [],
        "tx_hashes": [],
        "solana_addresses": [],
        "amounts_usd": [],
        "amounts_token": [],
        "percentages": [],
        "protocols": [],
    }

    def _add(key: str, value: str) -> None:
        if value not in claims[key]:
            claims[key].append(value)

    for match in _ADDRESS_RE.findall(text):
        _add("addresses", match.lower())
    for match in _TX_HASH_RE.findall(text):
        _add("tx_hashes", match.lower())
    for match in _SOLANA_RE.findall(text):
        # Skip strings already counted as EVM addresses/tx hashes.
        if match.lower() not in claims["addresses"] and match.lower() not in claims["tx_hashes"]:
            _add("solana_addresses", match)
    for match in _AMOUNT_USD_RE.findall(text):
        _add("amounts_usd", match.strip())
    for match in _AMOUNT_TOKEN_RE.findall(text):
        _add("amounts_token", match.strip())
    for match in _PERCENT_RE.findall(text):
        _add("percentages", match)
    for match in _PROTOCOL_RE.findall(text):
        _add("protocols", match.lower())
    return claims


def factual_density_score(text: str) -> int:
    """0-100: verifiable claims per 100 words.

    Articles with zero claims are treated as opinion/promotional copy and
    score a small base; each claim per 100 words adds ~30 points (capped).
    """
    words = len((text or "").split())
    if words < 10:
        return 0
    claims = _find_claims(text or "")
    total = sum(len(v) for v in claims.values())
    if total == 0:
        return 15
    claims_per_100 = total / words * 100
    return _clamp(claims_per_100 * 30)


# ═══════════════════════════════════════════════════════════════════════
# Timeliness
# ═══════════════════════════════════════════════════════════════════════


def _parse_datetime(value: Any) -> datetime | None:
    """Parse datetime/date/ISO-string/unix-timestamp inputs, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _age_hours(parsed: datetime, now: datetime) -> float:
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def timeliness_score(
    published_at: Any,
    event_at: Any = None,
    now: datetime | None = None,
) -> int:
    """0-100: fresh reporting scores high, stale re-reporting scores low.

    Piecewise decay on article age. When ``event_at`` is supplied and the
    article reports an event older than 72h, a re-report penalty applies.
    Missing ``published_at`` returns a neutral 50.
    """
    parsed = _parse_datetime(published_at)
    if parsed is None:
        return 50
    now_dt = now or datetime.now(UTC)
    age = _age_hours(parsed, now_dt)

    if age < 1:
        score = 100
    elif age < 6:
        score = 95
    elif age < 24:
        score = 88
    elif age < 72:
        score = 72
    elif age < 168:
        score = 55
    elif age < 720:
        score = 35
    else:
        score = 15

    event = _parse_datetime(event_at)
    if event is not None and event <= parsed:
        lag_hours = (parsed - event).total_seconds() / 3600.0
        if lag_hours > 72:
            score -= 15
    return _clamp(score)


# ═══════════════════════════════════════════════════════════════════════
# Bias detection
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# Corroboration
# ═══════════════════════════════════════════════════════════════════════


def corroboration_score(corroborating_sources: int | None) -> int:
    """0-100: more independent sources → higher corroboration.

    ``None``/unknown and 0 both score low; 5+ sources saturate.
    """
    if corroborating_sources is None:
        return 30
    n = max(0, int(corroborating_sources))
    if n >= 5:
        return 92
    if n == 4:
        return 85
    if n == 3:
        return 75
    if n == 2:
        return 60
    if n == 1:
        return 45
    return 30


# ═══════════════════════════════════════════════════════════════════════
# Cross-referencing
# ═══════════════════════════════════════════════════════════════════════

_CROSS_REF_KEY_RE = re.compile(
    r"\b(?:lost|stolen|hacked|exploited|drained|drained|attack)\b", re.IGNORECASE
)


def cross_reference_count(article: dict[str, Any]) -> int:
    """Estimate how many *trusted* independent sources report the event.

    Counts distinct trusted domains from the article's ``source_urls``,
    ``references``, ``related_articles`` and ``citations`` lists, and treats
    an explicit ``corroborating_sources`` field as authoritative when it is
    higher. ``aggregated`` articles count as at least two corroborating
    sources. A domain is trusted when it has an established/independent
    rating in ``SOURCE_REPUTATION``; unknown domains still count (an
    unknown blog corroborating a wire report is weak, so only trusted
    domains push the count past one).
    """
    explicit = article.get("corroborating_sources")
    if isinstance(explicit, int) and explicit > 0:
        return max(0, int(explicit))

    seen: set[str] = set()
    trusted: set[str] = set()
    for key in ("source_urls", "references", "related_articles", "citations"):
        for item in article.get(key) or []:
            if isinstance(item, str) and item.strip():
                domain = domain_from_url(item)
                if domain:
                    seen.add(domain)
                    if domain in SOURCE_REPUTATION:
                        trusted.add(domain)
            elif isinstance(item, dict):
                for sub in (item.get("url"), item.get("source"), item.get("title")):
                    if isinstance(sub, str) and sub.strip():
                        domain = domain_from_url(sub)
                        if domain:
                            seen.add(domain)
                            if domain in SOURCE_REPUTATION:
                                trusted.add(domain)
    count = len(seen)
    if article.get("aggregated"):
        count = max(count, 2)
    # Without at least one trusted domain, a lone unknown source is weak
    # (but an explicitly-aggregated article already counts as >= 2 sources).
    if count >= 1 and not trusted and not article.get("aggregated"):
        count = min(count, 1)
    return count


def cross_reference_score(article: dict[str, Any]) -> int:
    """0-100: independent trusted coverage of the same event.

    Thin wrapper over ``cross_reference_count`` so callers can compute the
    corroboration axis from the article dict alone.
    """
    return corroboration_score(cross_reference_count(article))


# ═══════════════════════════════════════════════════════════════════════
# Clickbait detection
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# On-chain verification
# ═══════════════════════════════════════════════════════════════════════

_CLAIM_USD_RE = re.compile(
    r"(?:\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(million|billion|thousand|m|bn|b|k)?\s?"
    r"(?:hacked|stolen|lost|drained|moved|exploited|taken)"
    r"|(?:hacked|stolen|lost|drained|moved|exploited|taken)\s+"
    r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(million|billion|thousand|m|bn|b|k)?)",
    re.IGNORECASE,
)
_CLAIM_TOKEN_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(ETH|BTC|SOL|USDT|USDC|BNB|XRP|DOGE|ADA|AVAX|MATIC|ARB|OP|TON)\b",
    re.IGNORECASE,
)


def on_chain_claim_verification(article: dict[str, Any]) -> dict[str, Any]:
    """Verify 'amount hacked' claims against on-chain artifacts in the text.

    Returns a dict with:
      - ``claims`` — list of parsed claims (amount, unit, verified)
      - ``verified`` — True when at least one claim is backed by an
        on-chain artifact (address/tx hash present in the article)
      - ``artifact_count`` — number of addresses/tx hashes found

    This is a *deterministic* verification: it does not call an RPC (the
    engine is pure Python and network-free by design). It treats the
    presence of a specific attacker address or transaction hash alongside
    an amount claim as on-chain evidence — the same heuristic the legacy
    ``on_chain_evidence`` flag used, now structured and per-claim.
    """
    text = _score_text(article)
    claims: list[dict[str, Any]] = []

    for m in _CLAIM_USD_RE.finditer(text):
        # Two alternatives: "$12.4m hacked" (g1=amount, g2=unit) or
        # "hacked $12.4m" (g3=amount, g4=unit). Exactly one side is set.
        amount_raw = (m.group(1) or m.group(3) or "").replace(",", "")
        unit = (m.group(2) or m.group(4) or "").lower()
        try:
            amount = float(amount_raw)
        except ValueError:
            continue
        if unit in ("million", "m"):
            amount *= 1_000_000
        elif unit in ("billion", "bn", "b"):
            amount *= 1_000_000_000
        elif unit in ("thousand", "k"):
            amount *= 1_000
        claims.append({"amount_usd": amount, "unit": unit, "verified": False})

    for m in _CLAIM_TOKEN_RE.finditer(text):
        amount_raw = m.group(1).replace(",", "")
        token = m.group(2).upper()
        try:
            amount = float(amount_raw)
        except ValueError:
            continue
        claims.append({"amount_token": amount, "token": token, "verified": False})

    artifacts = _find_claims(text)
    artifact_count = len(artifacts["addresses"]) + len(artifacts["tx_hashes"])
    verified = artifact_count > 0
    if verified:
        for claim in claims:
            claim["verified"] = True

    return {
        "claims": claims,
        "verified": verified,
        "artifact_count": artifact_count,
        "verification_method": "on-chain-artifacts",
    }


# ═══════════════════════════════════════════════════════════════════════
# Technical depth
# ═══════════════════════════════════════════════════════════════════════

TECH_TERMS: tuple[str, ...] = (
    "smart contract",
    "liquidity pool",
    "total value locked",
    "tvl",
    "governance",
    "vault",
    "staking",
    "yield",
    "impermanent loss",
    "oracle",
    "reentrancy",
    "gas fee",
    "gas price",
    "layer 2",
    "rollup",
    "zero-knowledge",
    "zk",
    "audit",
    "tokenomics",
    "supply schedule",
    "lockup",
    "vesting",
    "apr",
    "apy",
    "collateral",
    "liquidation",
    "delegation",
    "consensus",
    "proof of stake",
    "proof of work",
    "hashrate",
    "multisig",
    "amm",
    "swap",
    "bridge",
    "sidechain",
    "whale",
    "exchange netflow",
    "open interest",
    "funding rate",
    "block reward",
    "halving",
    "token unlock",
    "airdrop",
    "depeg",
    "stablecoin",
    "flash loan",
)
_TECH_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(term) for term in sorted(TECH_TERMS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def technical_depth_score(text: str) -> int:
    """0-100: blockchain-mechanics explanation density.

    Short articles (< 150 words) are capped at 60 — mechanics need room
    to breathe.
    """
    words = len((text or "").split())
    if words < 10:
        return 0
    hits = len(_TECH_RE.findall(text or ""))
    hits_per_100 = hits / words * 100
    score = _clamp(hits_per_100 * 40)
    if words < 150:
        score = min(score, 60)
    return score


# ═══════════════════════════════════════════════════════════════════════
# Source diversity
# ═══════════════════════════════════════════════════════════════════════


def _distinct_sources(article: dict[str, Any]) -> int:
    """Count distinct source references in the article dict.

    Recognises ``source_urls``, ``references``, ``related_articles`` and
    ``citations`` (each list of URLs/names). An ``aggregated`` flag counts
    as at least three independent sources.
    """
    seen: set[str] = set()
    for key in ("source_urls", "references", "related_articles", "citations"):
        for item in article.get(key) or []:
            if isinstance(item, str) and item.strip():
                seen.add(item.strip())
            elif isinstance(item, dict):
                for sub in (item.get("url"), item.get("source"), item.get("title")):
                    if isinstance(sub, str) and sub.strip():
                        seen.add(sub.strip())
    count = len(seen)
    if article.get("aggregated"):
        count = max(count, 3)
    return count


def source_diversity_score(article: dict[str, Any]) -> int:
    """0-100: multi-source / aggregated reporting scores higher."""
    count = _distinct_sources(article)
    if count == 0:
        return 50  # unknown — typical single-source article
    if count == 1:
        return 40
    if count == 2:
        return 60
    if count == 3:
        return 75
    return 90


# ═══════════════════════════════════════════════════════════════════════
# Composite + model
# ═══════════════════════════════════════════════════════════════════════


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


def _score_text(article: dict[str, Any]) -> str:
    parts = [str(article.get("title") or ""), str(article.get("content") or "")]
    return " ".join(p for p in parts if p)


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

    if composite >= 80:
        tier = "excellent"
    elif composite >= 65:
        tier = "good"
    elif composite >= 50:
        tier = "fair"
    else:
        tier = "poor"

    factuality: str
    if composite >= FACTUALITY_VERIFIED_MIN:
        factuality = "verified"
    elif composite >= FACTUALITY_REPORTED_MIN:
        factuality = "reported"
    else:
        factuality = "rumor"

    on_chain = bool(article.get("on_chain_evidence")) or (
        len(_find_claims(text)["addresses"] + _find_claims(text)["tx_hashes"]) > 0 and density >= 60
    )
    onchain_verification = on_chain_claim_verification(article)

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
