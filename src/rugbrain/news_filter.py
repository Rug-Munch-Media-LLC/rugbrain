"""news_filter — crypto-relevance classifier for news/social ingest.

Measured 2026-09-20: ``news_v2`` was 143,988 points with **71%** in the
``general`` bucket — generic Telegram traffic including Persian/Iranian news
channels with no crypto relevance (``t.me/IranintlTV``, ``khabarfuri``,
``farsna`` …). This module is the single classifier used by both the cleanup
script (``scripts/filter_news_noise.py``) and the ingest gate
(``scripts/news_to_rag.py``), so the two can never drift.

Verdicts:

* ``topical``        — category is a known crypto category → keep.
* ``crypto-general`` — general/unknown category but the text carries a crypto
  signal (contract address, ``$TICKER``, or a crypto keyword) → keep.
* ``noise``          — general/unknown category with no crypto signal → drop.
"""

from __future__ import annotations

import re
import unicodedata

TOPICAL_CATEGORIES = frozenset(
    {
        "news",
        "crypto",
        "market",
        "shiller",
        "ethereum",
        "bitcoin",
        "solana",
        "tron",
        "data",
        "x_social",
        "security",
        "scam_caller",
        "defi",
        "nft",
        "memecoin",
        "on-chain",
        "whales",
        "scams",
        "alerts",
    }
)

CRYPTO_TERMS = re.compile(
    r"\b(sol|solana|eth|ethereum|btc|bitcoin|token|coin|pump|dump|dex|cex|liquidity|rug|airdrop|"
    r"whale|wallet|defi|nft|meme|binance|coinbase|kraken|okx|bybit|market\s?cap|mcap|fdv|presale|"
    r"launch|staking|stake|swap|usdc|usdt|tvl|alpha|burn|mint|holders?|deployer|mainnet|testnet|"
    r"blockchain|crypto|onchain|on-chain|perps?|futures|spot|listing)\b",
    re.IGNORECASE,
)
TICKER = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,9}\b")
CONTRACT = re.compile(r"0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}")
NON_LATIN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u4E00-\u9FFF\u0400-\u04FF]")

#: English-only gate: >20% non-ASCII letters means the text is not English.
NON_ASCII_LETTER = re.compile(r"[^\x00-\x7F]")


def _normalize(text: str) -> str:
    """NFKC-fold styled Unicode (𝗦𝗢𝗟 -> SOL, fullwidth -> ASCII) before checks."""
    return unicodedata.normalize("NFKC", text)


def is_english(text: str, threshold: float = 0.2) -> bool:
    """True when the text is predominantly English (ASCII letters)."""
    text = _normalize(text)
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if NON_ASCII_LETTER.match(c))
    return (non_ascii / len(letters)) <= threshold


def classify_text(category: str | None, text: str) -> str:
    """Core classifier: 'topical' | 'crypto-general' | 'noise'.

    English-only + crypto-specific: non-English text is noise regardless of
    category; general/unknown text needs a crypto signal to be kept.
    """
    text = _normalize(text)
    if not is_english(text):
        return "noise"
    cat = str(category or "").strip().lower()
    if cat in TOPICAL_CATEGORIES:
        return "topical"
    if CONTRACT.search(text) or TICKER.search(text) or CRYPTO_TERMS.search(text):
        return "crypto-general"
    return "noise"


def classify_payload(payload: dict) -> str:
    """Classify a Qdrant point payload (metadata.category / content / title)."""
    md = payload.get("metadata") or {}
    category = md.get("category") or payload.get("category")
    text = f"{payload.get('content') or ''} {md.get('title') or ''}"
    return classify_text(str(category) if category else None, text)


def is_noise_article(article: dict) -> bool:
    """Ingest-side gate: True when the article should not be embedded."""
    text = " ".join(
        str(article.get(f) or "") for f in ("title", "summary", "body_markdown", "body", "content")
    )
    return classify_text(str(article.get("category") or ""), text) == "noise"


__all__ = ["TOPICAL_CATEGORIES", "classify_payload", "classify_text", "is_noise_article"]
