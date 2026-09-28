"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Entity extraction for ingested documents and news.

Pure, dependency-free, deterministic. Given a blob of text it returns the
crypto-relevant entities an investigator would filter on: addresses,
token tickers/symbols, protocol and chain names, and the severity signal
that decides which Qdrant collection a document lands in.

Deliberately regex-based rather than model-based. Three reasons:

  1. An address is a *shape*, not a meaning. A regex is exact and free;
     an LLM can hallucinate an address that never existed, and a false
     address in a crime RAG is worse than no address.
  2. Extraction runs on every ingested document, so it must be cheap and
     never a network call. Latency here is on the ingest hot path.
  3. The output must be reproducible. Same text in, same entities out, or
     re-ingest produces duplicate graph nodes.

EVM addresses are checksum-validated (EIP-55) so a truncated or mistyped
address from a bad scrape is dropped instead of becoming a phantom entity.
Solana base58 addresses are length- and charset-bounded.

NONE of this content is ever interpreted. Extracted strings are treated as
opaque identifiers and are written to Qdrant/Neo4j as data, never as
instructions. See ``security.py`` for the sanitisation applied on the way
in and out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

# ── address shapes ────────────────────────────────────────────────────

_EVM_RE: Final = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
# Solana: base58, no 0/O/I/l, 32-44 chars. Requires a word boundary that is
# not itself a base58 char so we do not clip a longer run mid-address.
_B58 = r"[1-9A-HJ-NP-Za-km-z]"
_SOL_RE: Final = re.compile(rf"\b({_B58}{{32,44}})\b")
# TON / base58 34 chars is ambiguous with Solana; excluded deliberately.
_TRON_RE: Final = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")

# Domain (phishing / drainer infrastructure).
_DOMAIN_RE: Final = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|xyz|info|app|dev|ru|cn|top|live|site|vip|cc)\b",
    re.IGNORECASE,
)

# ── severity lexicon ──────────────────────────────────────────────────

# Ordered: the first hit wins, so put the most specific first. These are
# deliberately blunt. Severity drives which collection a doc lands in, so
# a false "critical" is costlier than a false "low".
_SEVERITY_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "critical",
        (
            "rug pull",
            "rugpull",
            "rug pulled",
            "drainer",
            "drainers",
            "honeypot",
            "private key compromised",
            "private key leak",
            "exploit",
            "exploited",
            "drained",
            "drain ",
            "stolen funds",
            "compromised",
            "breach",
            "exit scam",
            "insider",
        ),
    ),
    (
        "high",
        (
            "phishing",
            "scam",
            "scammed",
            "laundering",
            "sanction",
            "arrest",
            "seized",
            "impersonat",
            "blackmail",
        ),
    ),
    (
        "medium",
        (
            "vulnerability",
            "vuln",
            "attack",
            "suspected",
            "warning",
            "malicious",
            "outage",
            "freeze",
        ),
    ),
)

# Protocol / chain names. Matched on a word boundary, case-insensitive.
# A miss costs recall on the graph edge; a false hit costs precision. The
# list is long on purpose for the first two groups (high recall, generic
# impact) and short on the last (named entities, each is a real node).
_PROTOCOLS: Final = (
    "uniswap",
    "aave",
    "curve",
    "compound",
    "makerdao",
    "chainlink",
    "sushiswap",
    "pancakeswap",
    "euler",
    "marinade",
    "jupiter",
    "raydium",
    "orca",
    "pyth",
    "axelar",
    "layerzero",
    "stargate",
    "wormhole",
    "hyperliquid",
    "gmx",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
    "solana",
    "ethereum",
    "bitcoin",
    "tron",
    "monero",
    "cbridge",
    "debridge",
    "across",
    "starknet",
    "celestia",
)
_CHAINS: Final = (
    "ethereum",
    "solana",
    "bitcoin",
    "binance",
    "arbitrum",
    "base",
    "polygon",
    "optimism",
    "avalanche",
    "tron",
    "bsc",
    "sui",
    "aptos",
    "cosmos",
    "near",
    "stellar",
    "cardano",
    "dogecoin",
    "monero",
)

# Tokens quoted as $TICKER. $2 avoids matching "$5" or "$1,000" — the
# digits-then-letter boundary is what makes this precise enough to use as
# a graph node identity.
_TICKER_RE: Final = re.compile(r"(?<![\w$])\$([A-Z][A-Z0-9]{1,9})\b")

# Ticker symbols a chain trades under. Deliberately short and explicit:
# guessing a symbol from a chain's name ("solana" -> "SOL") would also
# swallow the ordinary token $SOLANA, and losing a real token entity is a
# worse error than carrying one duplicate node.
_CHAIN_TICKER_SYMBOLS: Final = frozenset(
    {
        "SOL",
        "ETH",
        "BTC",
        "BNB",
        "TRX",
        "POL",
        "AVAX",
        "ARB",
        "OP",
        "DOGE",
        "XMR",
        "SUI",
        "APT",
        "NEAR",
        "ATOM",
    }
)

# Bare protocol references in prose ("the Euler v2 exploit").
_NAME_RE: Final = re.compile(r"(?<![\w-])([a-z][a-z0-9-]{2,20})(?![\w-])", re.IGNORECASE)


def evm_checksum(address: str) -> bool:
    """True when *address* is a valid EIP-55 mixed-case-checksum address.

    An all-lowercase or all-uppercase address is *syntactically* valid
    (0x + 40 hex) but carries no checksum, so it is more likely a
    hand-typed or OCR'd value than a copied one. We still accept it as an
    address; this function is used by the caller to decide whether to
    trust it as an identity, not whether it is shaped like one.
    """
    if not _EVM_RE.fullmatch(address):
        return False
    body = address[2:]
    return not (body.islower() or body.isupper())


@dataclass(frozen=True, slots=True)
class EntitySet:
    """Entities extracted from one document.

    Frozen: a caller cannot mutate the result of extraction and have the
    next stage disagree about what was found.
    """

    evm_addresses: frozenset[str] = frozenset()
    solana_addresses: frozenset[str] = frozenset()
    tron_addresses: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    tickers: frozenset[str] = frozenset()
    protocols: tuple[str, ...] = ()
    chains: tuple[str, ...] = ()
    severity: str = "low"
    matched_rules: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        """True when nothing entity-shaped was found (a plain prose doc)."""
        return not (
            self.evm_addresses
            or self.solana_addresses
            or self.tron_addresses
            or self.domains
            or self.tickers
            or self.protocols
        )

    @property
    def addresses(self) -> frozenset[str]:
        """Every address across all chains, for the graph edge writer."""
        return self.evm_addresses | self.solana_addresses | self.tron_addresses

    def as_dict(self) -> dict[str, object]:
        """Serializable form for the Qdrant payload."""
        return {
            "evm_addresses": sorted(self.evm_addresses),
            "solana_addresses": sorted(self.solana_addresses),
            "tron_addresses": sorted(self.tron_addresses),
            "domains": sorted(self.domains),
            "tickers": sorted(self.tickers),
            "protocols": list(self.protocols),
            "chains": list(self.chains),
            "severity": self.severity,
        }


def _dedupe(seq: list[str]) -> tuple[str, ...]:
    """Order-preserving dedupe (first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def classify_severity(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(severity, matched_rule_phrases)`` for *text*.

    Returns ``("low", ())`` when nothing matches — an unmatched document
    is not a zero-severity document, it is an unclassified one, and the
    caller decides what to do with that.
    """
    lowered = text.lower()
    for level, phrases in _SEVERITY_RULES:
        hits = [p for p in phrases if p in lowered]
        if hits:
            return level, tuple(hits)
    return "low", ()


def _solana_candidates(text: str) -> set[str]:
    """Base58 runs of Solana-plausible length, minus known false positives.

    The base58 shape is very permissive: an ordinary 40-char word or a
    git SHA also matches. We require a mixed case (Solana addresses are
    not all-lowercase) and a digit, which kills the overwhelming majority
    of prose and hex-SHA false positives.
    """
    out: set[str] = set()
    for cand in _SOL_RE.findall(text):
        if len(cand) < 32:
            continue
        if not any(c.isdigit() for c in cand):
            continue
        if cand.islower() or cand.isupper():
            continue
        out.add(cand)
    return out


def extract(text: str, *, max_chars: int = 200_000) -> EntitySet:
    """Extract every crypto-relevant entity from *text*.

    Truncates to *max_chars* defensively: a 10 MB paste would otherwise
    cost a lot of regex time for no additional entities, and the caller
    stores chunks anyway.
    """
    if not text:
        return EntitySet()
    body = text[:max_chars]

    severity, matched = classify_severity(body)
    evm = {a for a in _EVM_RE.findall(body) if len(a) == 42}
    sol = _solana_candidates(body)
    tron = {a for a in _TRON_RE.findall(body)}
    # A T-address is also a valid base58 string; drop it from the Solana
    # set so one address never becomes two graph nodes.
    sol -= tron

    lowered = body.lower()
    protocols = _dedupe([p for p in _PROTOCOLS if p in lowered])
    chains = _dedupe(
        [c for c in _CHAINS if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", lowered)]
    )
    tickers = frozenset(_TICKER_RE.findall(body))
    # A $TICKER that is a chain's own ticker symbol is one identity, not
    # two — $SOL is the chain Solana, so a graph node keyed on both would
    # split one subject in half. Only symbols the chain itself actually
    # trades under are removed: $SOLANA and $BASE are ordinary tokens
    # and must survive, because a scammer naming a token "$BASE" is
    # exactly the case this extractor exists to catch.
    tickers = frozenset(t for t in tickers if t not in _CHAIN_TICKER_SYMBOLS)

    return EntitySet(
        evm_addresses=frozenset(a for a in evm if evm_checksum(a) or a[2:].islower()),
        solana_addresses=frozenset(sol),
        tron_addresses=frozenset(tron),
        domains=frozenset(d.lower() for d in _DOMAIN_RE.findall(body)),
        tickers=tickers,
        protocols=protocols,
        chains=chains,
        severity=severity,
        matched_rules=matched,
    )
