"""Constants for the RAG crawler (extracted from ``app/rag/crawler.py``).

Degodding split (2026-08-13): the crawl/relevance vocabulary and sweep
knobs live here so ``crawler.py`` stays focused on the crawl lifecycle
(discovery → quality gates → ingest). ``crawler.py`` re-exports every
public name so ``crawler.<name>`` references in tests and callers keep
resolving.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

# Optional unified source registry (category → feed URLs). Env-driven so
# deployments can point at their own registry without editing the package.
_SOURCES_FILE = Path(os.getenv("RAG_UNIFIED_SOURCES_FILE", "") or "")
# Package-local crawl list (user-supplied; example schema in config/).
_LEGACY_SOURCES_FILE = Path(__file__).resolve().parents[1] / "config" / "crawl_sources.json"
# Source types that yield article feeds the crawler can follow. Reddit and
# nitter sources in the registry are RSS feeds too (``reddit-*``/``twitter-*``
# entries in unified_news_sources.json, 36 sources) — they are wired into
# discovery so the social streams are consumed instead of sitting dead. The
# sweep's quality gates (spam/relevance/freshness/English/quality) still apply
# to every discovered item, and crawl failures fail open per source.
_CRAWLABLE_TYPES = ("rss", "atom", "sitemap", "reddit", "nitter")
DEFAULT_COLLECTION = "news"
DEFAULT_PAGE_TIMEOUT_MS = 45_000
DEFAULT_MAX_URLS_PER_SWEEP = 25

# ── Public scam/rug/exploit incident sources (RAG enrichment) ────────────────

# Registry of structured scam/exploit data feeds — see crawl_sources.json under
# the ``incident_sources`` key. Each entry: {name, description, type, url,
# category, collections, enabled}. ``type: json_api`` sources are fetched and
# normalized by app.rag.ingest_crypto_knowledge.ingest_incidents_from_sources;
# ``type: html_page`` entries document page-based sources that need a
# crawl/parse step before they can be wired (kept disabled until a parser
# exists).
INCIDENT_SOURCES_FILE = Path(
    os.getenv("RAG_CRAWL_SOURCES_FILE", "")
    or str(Path(__file__).resolve().parents[1] / "config" / "crawl_sources.json")
)

# Allowed incident categories (rug / exploit / scam).
INCIDENT_CATEGORIES: tuple[str, ...] = ("rug", "exploit", "scam", "education")

# category → target RAG collections (neutral defaults; deployments map these
# to their own collection namespaces).
INCIDENT_COLLECTION_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "rug": ("scam_patterns", "scam_docs"),
    "exploit": ("exploit_docs", "threat_intel"),
    "scam": ("scam_patterns", "scam_docs"),
    "education": ("knowledge", "threat_intel"),
}

# Default category per known source — used by ``normalize_incident`` when a raw
# record does not carry an explicit category field.
INCIDENT_CATEGORY_BY_SOURCE: dict[str, str] = {
    "defillama_hacks": "exploit",
    "rekt_news_leaderboard": "exploit",
    "slowmist_hacked": "scam",
    "certik_security_alerts": "exploit",
    "metamask_eth_phishing_detect": "scam",
    "defihacklabs_incidents": "exploit",
    "ethereum_labels_0x406": "scam",
    # Crypto documentation / knowledge sources (2026-08-14) — category
    # "education" maps to knowledge + threat_intel. Raw markdown /
    # RST / AsciiDoc files fetched from stable unauthenticated raw URLs.
    "consensys_best_practices": "education",
    "not_so_smart_contracts": "education",
    "smart_contract_vulnerabilities": "education",
    "solidity_security_considerations": "education",
    "openzeppelin_upgradeable_docs": "education",
}

# Dedup counter keys returned in sweep summaries (distinguish dup levels).
DUP_COUNTERS = ("url_dup", "title_dup", "content_dup", "entity_dup")

# Article quality / relevance / freshness knobs (sweep pipeline).
MAX_ARTICLES_PER_SOURCE = 10  # per-source cap per sweep (rate limiting)
MIN_ARTICLE_QUALITY = 0.3  # skip articles scoring below this (0-1)
MAX_ARTICLE_AGE_HOURS = 6.0  # older articles are stale → skip
FRESH_AGE_HOURS = 1.0  # <1h = fresh → ingest immediately

# ── Relevance / quality vocab ────────────────────────────────────────────────

# Uppercase token symbols matched as whole words (case-sensitive) so common
# prose words ("op", "ton", "arb") do not trip the relevance filter.
_TOKEN_SYMBOLS: tuple[str, ...] = (
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "DOT",
    "AVAX",
    "LINK",
    "MATIC",
    "POL",
    "USDC",
    "USDT",
    "DAI",
    "LTC",
    "BCH",
    "SHIB",
    "PEPE",
    "WIF",
    "BONK",
    "SUI",
    "APT",
    "ARB",
    "OP",
    "TON",
    "TRX",
    "NEAR",
    "ATOM",
    "INJ",
    "SEI",
    "JUP",
    "UNI",
    "AAVE",
    "MKR",
    "LDO",
    "CRV",
    "GMX",
    "SNX",
    "PYTH",
    "WLD",
    "ENA",
    "PENDLE",
    "EIGEN",
    "STRK",
    "ZRO",
    "RENDER",
    "TAO",
    "KAS",
    "XLM",
    "ALGO",
    "HBAR",
    "ETC",
    "IMX",
)

_PROTOCOL_WORDS: tuple[str, ...] = (
    "bitcoin",
    "ethereum",
    "solana",
    "cardano",
    "polkadot",
    "avalanche",
    "polygon",
    "arbitrum",
    "optimism",
    "chainlink",
    "uniswap",
    "aave",
    "lido",
    "makerdao",
    "curve finance",
    "synthetix",
    "compound",
    "pancakeswap",
    "jupiter",
    "raydium",
    "celestia",
    "binance",
    "coinbase",
    "tether",
    "stablecoin",
)

_DEFI_WORDS: tuple[str, ...] = (
    "staking",
    "yield",
    "liquidity",
    "dex",
    "defi",
    "decentralized finance",
    "amm",
    "lending",
    "borrowing",
    "farming",
    "restaking",
    "perpetual",
    "oracle",
    "bridge",
    "liquidity pool",
    "tvl",
    "vault",
)

_SECURITY_WORDS: tuple[str, ...] = (
    "hack",
    "hacked",
    "hacking",
    "exploit",
    "audit",
    "vulnerability",
    "breach",
    "scam",
    "rug pull",
    "rugpull",
    "phishing",
    "stolen",
    "theft",
    "drain",
    "drained",
    "zero-day",
    "malware",
    "honeypot",
    "wallet drainer",
    "attack",
    "compromised",
)

_AGENCY_WORDS: tuple[str, ...] = ("SEC", "CFTC", "MiCA", "FinCEN", "OCC", "DOJ")
_REGULATORY_WORDS: tuple[str, ...] = (
    "regulation",
    "regulatory",
    "enforcement",
    "lawsuit",
    "indictment",
    "sanction",
    "subpoena",
    "probe",
    "banning",
    "banned",
    "ETF",
    "approved",
)

_TOKEN_RE = re.compile(r"\b(?:" + "|".join(_TOKEN_SYMBOLS) + r")\b")
_PROTOCOL_RE = re.compile(r"\b(?:" + "|".join(_PROTOCOL_WORDS) + r")\b", re.IGNORECASE)
_DEFI_RE = re.compile(r"\b(?:" + "|".join(_DEFI_WORDS) + r")\b", re.IGNORECASE)
_SECURITY_RE = re.compile(r"\b(?:" + "|".join(_SECURITY_WORDS) + r")\b", re.IGNORECASE)
_AGENCY_RE = re.compile(r"\b(?:" + "|".join(_AGENCY_WORDS) + r")\b")
_REGULATORY_RE = re.compile(r"\b(?:" + "|".join(_REGULATORY_WORDS) + r")\b", re.IGNORECASE)

# Paywall / teaser markers — content that is only a locked summary.
_PAYWALL_MARKERS: tuple[str, ...] = (
    "subscribe to read",
    "sign in to read",
    "sign in to continue",
    "login to read",
    "premium content",
    "premium article",
    "members only",
    "this is a preview",
    "unlock full",
    "become a member",
    "subscribe for full",
    "paywall",
    "registration required",
    "full story requires",
)

# ── News content quality gates ─────────────────────────────────────────────
# Spam / clickbait, HTML boilerplate extraction, summarisation, title
# cleaning, and language detection. These keep low-quality content out of
# the news collection so ratings and search stay trustworthy.

# Phrases that signal promotional clickbait — skip the article entirely.
SPAM_INDICATORS: tuple[str, ...] = (
    "price prediction",
    "will explode",
    "next 100x",
    "don't miss",
    "urgent",
    "breaking: price",
    "just in: price",
    "you won't believe",
    "guaranteed",
    "risk free",
    "secret method",
)

# Sources known for recycled / promotional content — never rate or ingest.
LOW_QUALITY_DOMAINS: tuple[str, ...] = (
    "zycrypto.com",
    "coinpedia.org",
    "bitcoinist.com",
    "cryptopotato.com",
    "coingape.com",
)

# Elements whose whole subtree is boilerplate — never part of the article body.
_BOILERPLATE_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "template",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "dialog",
        "select",
        "input",
        "textarea",
    }
)

# Class/id fragments (case-insensitive substring) that mark boilerplate
# containers: cookie banners, newsletter signups, related-article rails,
# sidebars, share widgets, popups, and navigation.
_BOILERPLATE_FRAGMENTS: tuple[str, ...] = (
    "cookie",
    "consent",
    "newsletter",
    "signup",
    "subscribe",
    "related",
    "recommended",
    "sidebar",
    "advert",
    "-ad",
    "promo",
    "share",
    "social",
    "breadcrumb",
    "popup",
    "modal",
    "overlay",
    "trending",
    "most-read",
    "comments",
    "widget",
    "menu",
    "navigation",
)

# Class/id fragments that mark the primary article body container.
_CONTENT_FRAGMENTS: tuple[str, ...] = (
    "article-body",
    "article_content",
    "article-content",
    "post-content",
    "post_content",
    "entry-content",
    "entry_content",
    "story-body",
    "story_content",
    "news-body",
    "single-post",
    "post-body",
    "page-content",
)

# Block-level tags whose boundary implies a paragraph break in extracted text.
_BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "li",
        "blockquote",
        "figure",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
        "hr",
        "table",
        "ul",
        "ol",
    }
)

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^<>]*?)?)(/?)>", re.DOTALL)

# Language gate: a word counts as "ASCII" when every code point is < 128.
# CJK text has no inter-word spaces, so the word count is naturally low —
# 3 is enough to distinguish a real foreign-language paragraph.
_ENGLISH_MIN_WORDS = 3
_ENGLISH_ASCII_THRESHOLD = 0.8

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
