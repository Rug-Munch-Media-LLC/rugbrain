"""Deterministic news classifier: category + source-trust weight.

No LLM per item. The routing layer (rugbrain) needs a cheap, stable, explainable
label on every article so it can rank, filter and alert without paying for a
model call on thousands of items a day.

Categories (first match wins, most specific first):
  exploit     — hacks, drains, bridge/contract exploits, post-mortems
  scam        — rugs, honeypots, phishing, fake tokens, arrests
  regulation  — SEC/CFTC/OFAC/DOJ/court/ETF/lawsuit/licence actions
  research    — reports, on-chain analytics, papers, protocol deep-dives
  shilling    — airdrops, presales, "100x", partnerships hype, listings pumps
  market      — price, ETF flows, volume, funding, liquidation, dominance
  news        — everything else

Trust weight 0.0–1.0 is a *source* property, not an item property: primary
filings and research desks outrank aggregator reposts. Values are intentionally
coarse — ranking multiplies them, it must not pretend to be precision.

Usage:
    from rugbrain.rss_classify import classify
    category, trust = classify(title="...", summary="...", source="Chainalysis")
"""

from __future__ import annotations

import re

# Ordered: (category, patterns). Regex, case-insensitive; word boundaries keep
# "hackathon" out of "hack" and "scam" out of "scanner".
RULES: list[tuple[str, str]] = [
    (
        "exploit",
        r"\b(exploit(ed)?|hack(ed|er|ing)?|drain(ed|er)?|reentrancy|flash[- ]loan attack|"
        r"bridge attack|private key (leak|compromis)|post[- ]mortem|vulnerabilit(y|ies)|"
        r"zero[- ]day|rug ?pull|malicious contract|oracle manipulation|stolen funds)\b",
    ),
    (
        "scam",
        r"\b(scam(mer|med|s)?|fraud|phishing|ponzi|honeypot|fake token|impersonat|"
        r"pig butchering|money laundering|charged|arrested|indicted|sanction(s|ed)? evasion|"
        r"seized|confiscat)\b",
    ),
    (
        "regulation",
        r"\b(sec |cftc|ofac|fincen|doj\b|treasury|mica\b|esma|fca\b|regulat(or|ion|ory)|"
        r"lawsuit|settlement|court|judge|ruling|appeal|licen[cs]e|etf approval|"
        r"executive order|legislation|bill passes|hearing|subpoena)\b",
    ),
    (
        "research",
        r"\b(report|research|study|analysis|thesis|on[- ]chain data|metric(s)? show|"
        r"whitepaper|paper|survey|benchmark|deep[- ]dive|dashboard|glassnode|"
        r"kaiko|messari|chainalysis|nansen)\b",
    ),
    (
        "shilling",
        r"\b(airdrop|presale|100x|1000x|to the moon|next gem|gem alert|"
        r"partnership announced|strategic partnership|listing announcement|"
        r"will list|now listed|giveaway|whitelist|meme coin launch|fair launch)\b",
    ),
    (
        "market",
        r"\b(price|all[- ]time high|ath\b|rall(y|ies)|surge|plunge|crash|dump|pump|"
        r"liquidation|funding rate|open interest|volume|market cap|dominance|"
        r"etf (inflow|outflow|flow)|longs?|shorts?|support|resistance)\b",
    ),
]

_COMPILED = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in RULES]

# Tier-0: primary documents. Tier-1: research desks and security firms.
# Tier-2: major press. Tier-3: aggregators/aggregator-style reposts.
SOURCE_TRUST: dict[str, float] = {
    "sec press releases": 1.0,
    "ofac recent actions": 0.98,
    "cftc press releases": 0.98,
    "doj news": 0.96,
    "federal reserve": 0.95,
    "fca news": 0.95,
    "esma": 0.95,
    "chainalysis": 0.95,
    "chainalysis blog": 0.95,
    "chainalysis reports": 0.95,
    "glassnode": 0.93,
    "glassnode insights": 0.93,
    "kaiko research": 0.93,
    "messari": 0.92,
    "coin metrics": 0.92,
    "coinmetrics": 0.92,
    "nansen": 0.9,
    "defillama": 0.9,
    "dune blog": 0.88,
    "ethereum foundation": 0.9,
    "eth research": 0.88,
    "eth magicians": 0.85,
    "ethresear.ch": 0.85,
    "slowmist": 0.92,
    "peckshield": 0.92,
    "certik": 0.88,
    "blocksec": 0.88,
    "rekt news": 0.85,
    "immunefi": 0.88,
    "trail of bits": 0.9,
    "openzeppelin": 0.9,
    "halborn": 0.85,
    "quantstamp": 0.85,
    "dedaub": 0.85,
    "hexens": 0.85,
    "osec": 0.82,
    "the block": 0.85,
    "coindesk": 0.85,
    "cointelegraph": 0.8,
    "blockworks": 0.8,
    "decrypt": 0.78,
    "the defiant": 0.75,
    "dl news": 0.78,
    "protos": 0.75,
    "unchained": 0.78,
    "bitcoin magazine": 0.72,
    "crypto.news": 0.68,
    "cryptoslate": 0.65,
    "beincrypto": 0.62,
    "coingape": 0.6,
    "coinjournal": 0.6,
    "newsbtc": 0.55,
    "dailycoin": 0.55,
    "coinpedia": 0.5,
    "amb crypto": 0.5,
    "cryptobriefing": 0.55,
    "finbold": 0.5,
    "daily hodl": 0.45,
    "the daily hodl": 0.45,
    "zycrypto": 0.4,
    "nulltx": 0.4,
    "bitcoinist": 0.45,
    "blockonomi": 0.45,
    "coinspeaker": 0.4,
    "cryptopolitan": 0.35,
    "insidebitcoins": 0.3,
    "cryptonews": 0.45,
    "u.today": 0.4,
    "trustnodes": 0.4,
    "the cryptobasic": 0.35,
    "cryptoadventure": 0.3,
    "live bitcoin news": 0.3,
    "crypto daily": 0.3,
    "cryptodaily": 0.3,
    "techcrunch": 0.7,
    "ars technica": 0.72,
    "wired": 0.68,
    "the hacker news": 0.7,
    "bleepingcomputer": 0.7,
    "krebs on security": 0.8,
    "cisa advisories": 0.92,
}
DEFAULT_TRUST = 0.5
AGGREGATOR_TRUST = 0.35


def trust_for(source: str) -> float:
    """Source-trust weight in [0,1]; unknown sources get the neutral default."""
    key = (source or "").strip().lower()
    if key in SOURCE_TRUST:
        return SOURCE_TRUST[key]
    for name, weight in SOURCE_TRUST.items():
        if name in key:
            return weight
    return AGGREGATOR_TRUST if any(w in key for w in ("aggregat", "feed", "rss")) else DEFAULT_TRUST


def classify(title: str, summary: str = "", source: str = "") -> tuple[str, float]:
    """Return ``(category, source_trust)`` for one item. Deterministic, no I/O."""
    text = f"{title or ''}\n{summary or ''}"
    for category, pattern in _COMPILED:
        if pattern.search(text):
            return category, trust_for(source)
    return "news", trust_for(source)
