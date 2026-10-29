"""High-signal detection primitives for prompt compression.

Extracted verbatim from the 734-line ``prompt_compression.py``: token
estimation, the address/code/filler regexes, the scored ``_PromptUnit`` and
the protect/split/score helpers. One job per module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── token estimation (matches app_chunking's ~4 chars/token) ──────────────

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (``~4 chars/token``), no tokenizer dependency."""
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


# ── high-signal detection ─────────────────────────────────────────────────

_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# EVM-ish hex (covers 0x...40 contracts), Solana base58, Bitcoin.
_ADDRESS_RE = re.compile(
    r"\b0x[a-fA-F0-9]{6,64}\b"
    r"|[1-9A-HJ-NP-Za-km-z]{32,44}"
    r"|\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b"
)
_PLACEHOLDER_RE = re.compile(r"\x00([AC])(\d+)\x00")

_HEADER_RE = re.compile(r"^#{1,6}\s")
_HAS_NUMBER_RE = re.compile(r"\d")
_CODE_KEYWORD_RE = re.compile(
    r"\b(contract|function|interface|library|struct|enum|impl|trait|"
    r"pub fn|def |class |constructor|modifier|mapping|address payable|"
    r"uint256|require|assert|mut |unsafe|async fn|return|import |from |"
    r"pragma|solc)\b"
)
_VULN_TERMS = (
    "reentrancy",
    "rug pull",
    "rugpull",
    "honeypot",
    "drain",
    "exploit",
    "vulnerability",
    "phishing",
    "scam",
    "malicious",
    "attacker",
    "stolen",
    "hack",
    "breach",
    "unauthorized",
    "backdoor",
    "arbitrage",
    "slippage",
    "oracle manipulation",
    "flash loan",
    "approve",
    "allowance",
    "private key",
    "seed phrase",
    "liquidity removed",
    "lp removed",
    "kill switch",
    "upgradeable",
)
_FILLER_START_RE = re.compile(
    r"^(however|moreover|furthermore|additionally|in addition|"
    r"it is important|please note|note that|as you can see|let.s|"
    r"in conclusion|overall|in summary|for example|that being said|"
    r"having said that|it should be noted|we can see|essentially|"
    r"basically|interestingly|importantly|on the other hand|"
    r"in other words|as mentioned|as noted)\b",
    re.IGNORECASE,
)
_BOILERPLATE_RE = re.compile(
    r"\b(stay tuned|follow us|join our telegram|follow @|disclaimer|"
    r"not financial advice|do your own research|dyor|subscribe to)\b",
    re.IGNORECASE,
)

_CODE_BLOCK_SCORE = 12.0


@dataclass(frozen=True)
class _PromptUnit:
    """A sentence or atomic code block with a signal score."""

    text: str
    score: float
    order: int
    is_code: bool = False


def _protect_spans(
    text: str, *, protect_addresses: bool = True
) -> tuple[str, list[str], list[str]]:
    """Replace addresses / inline code with placeholders; return ``(text, addresses, code_spans)``."""
    addresses: list[str] = []
    code_spans: list[str] = []

    if protect_addresses:

        def _addr_repl(m: re.Match[str]) -> str:
            addresses.append(m.group(0))
            return f"\x00A{len(addresses) - 1}\x00"

        text = _ADDRESS_RE.sub(_addr_repl, text)

    def _code_repl(m: re.Match[str]) -> str:
        code_spans.append(m.group(0))
        return f"\x00C{len(code_spans) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_code_repl, text)
    return text, addresses, code_spans


def _unprotect(text: str, addresses: list[str], code_spans: list[str]) -> str:
    """Restore addresses / code spans from placeholders in *text*."""

    def _repl(m: re.Match[str]) -> str:
        pool = addresses if m.group(1) == "A" else code_spans
        idx = int(m.group(2))
        return pool[idx] if 0 <= idx < len(pool) else ""

    return _PLACEHOLDER_RE.sub(_repl, text)


def _split_sentences(text: str) -> list[str]:
    """Split into line/sentence fragments without breaking protected spans."""
    parts: list[str] = []
    for line in re.split(r"\r?\n+", text):
        line = line.strip()
        if not line:
            continue
        # Header / label lines ("## Risks", "Summary:") are atomic units.
        if _HEADER_RE.match(line) or line.endswith(":"):
            parts.append(line)
            continue
        for frag in re.split(r"(?<=[.!?])\s+", line):
            if frag.strip():
                parts.append(frag.strip())
    return parts


def _score_unit(text: str, *, has_addr: bool, has_code_span: bool) -> float:
    """Signal score: higher = kept first when compressing."""
    low = text.lower()
    score = 1.0
    if has_addr:
        score += 3.0
    if has_code_span:
        score += 1.5
    if _HAS_NUMBER_RE.search(text):
        score += 1.5
    if _HEADER_RE.search(low) or text.rstrip().endswith(":"):
        score += 2.0
    if _CODE_KEYWORD_RE.search(low):
        score += 1.5
    if any(term in low for term in _VULN_TERMS):
        score += 2.0
    if _FILLER_START_RE.match(low):
        score -= 2.0
    if _BOILERPLATE_RE.search(low):
        score -= 2.0
    return score
