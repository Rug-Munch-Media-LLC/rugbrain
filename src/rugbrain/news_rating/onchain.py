"""On-chain claim verification against artifacts present in the text."""

from __future__ import annotations

import logging
import re
from typing import Any

from rugbrain.news_rating.factual import _find_claims

logger = logging.getLogger(__name__)

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


def _score_text(article: dict[str, Any]) -> str:
    parts = [str(article.get("title") or ""), str(article.get("content") or "")]
    return " ".join(p for p in parts if p)


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
            logger.debug("onchain: unparseable claim amount %r", amount_raw)
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
            logger.debug("onchain: unparseable claim amount %r", amount_raw)
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
