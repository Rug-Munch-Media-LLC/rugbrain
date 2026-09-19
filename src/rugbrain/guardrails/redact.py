"""Output guardrail — secret / hostname redaction."""

from __future__ import annotations

import os
import re

from ._wordlist import BIP39_MARKER_WORDS

# EVM private keys: 0x + 64 hex chars.
_EVM_KEY_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
# 64-hex without 0x prefix, only when labeled as a key (avoid nuking
# content that legitimately embeds 64-hex hashes).
_LABELED_HEX_KEY_RE = re.compile(
    r"\b(?:private\s+key|secret\s+key|privkey|pkey|skey|key)\s*[:=]\s*(?:0x)?[0-9a-fA-F]{64}\b",
    re.IGNORECASE,
)


def _redact_mnemonics(text: str) -> str:
    """Redact 12/24-word BIP39-style runs (majority-marker heuristic)."""
    # Match runs of exactly 12 or 24 lowercase alpha words (3-8 chars).
    # Word boundaries prevent partial-word matches. 24-word alternative is
    # tried first so a 24-word mnemonic is redacted as one unit, not two 12s.
    pattern = re.compile(
        r"\b(?:(?:[a-z]{3,8})\s+){23}[a-z]{3,8}\b"
        r"|\b(?:(?:[a-z]{3,8})\s+){11}[a-z]{3,8}\b"
    )

    def _replace(match: re.Match[str]) -> str:
        words = match.group(0).split()
        if len(words) not in (12, 24):
            return match.group(0)
        hits = sum(1 for w in words if w in BIP39_MARKER_WORDS)
        if hits * 2 >= len(words):  # majority of tokens are BIP39 markers
            return "[REDACTED]"
        return match.group(0)

    return pattern.sub(_replace, text)


# API-key-looking strings: common prefixes + generic key=value pairs.
_API_KEY_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}|"  # OpenAI-style
    r"sk_live_[A-Za-z0-9]{20,}|"  # Stripe-style
    r"AKIA[0-9A-Z]{16}|"  # AWS access key
    r"AIza[0-9A-Za-z_-]{35}|"  # Google API key
    r"ghp_[A-Za-z0-9]{30,}|"  # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack token
    r")\b",
)
# Generic labeled secrets. Bare "token" is intentionally NOT a label here —
# "token: 0x…" in crypto content is usually a token *address*, not a secret.
_GENERIC_KEY_RE = re.compile(
    r"\b(?:api[_-]?key|apikey|secret|password|passwd|pwd|"
    r"access[_-]?token|auth[_-]?token|bearer)\s*[=:]\s*[\"']?[A-Za-z0-9_./+-]{16,}[\"']?",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}\b", re.IGNORECASE)
# Internal fleet hostnames are configured via RMI_INTERNAL_HOSTS (a
# comma-separated list of host labels) so no deployment-specific hostnames
# ship in the public repo. Default is EMPTY — with no hosts configured only
# the generic patterns below (RFC1918/CGNAT IPs, *.internal / *.local
# FQDNs) are redacted. A configured label is redacted both as a bare word
# and as <label>.rugmunch.io. The public rugmunch.io domain itself is NOT
# redacted (see [REVIEW]).
# ``*.internal`` / ``*.local`` require at least one prefix label — a bare
# ``internal`` is a common Solidity visibility keyword and must survive
# (fix: `+` quantifier; `*` would redact it and corrupt code snippets).
_INTERNAL_HOSTS: tuple[str, ...] = tuple(
    h.strip().lower() for h in os.getenv("RMI_INTERNAL_HOSTS", "").split(",") if h.strip()
)

_INTERNAL_FQDN_PARTS: list[str] = []
if _INTERNAL_HOSTS:
    _INTERNAL_FQDN_PARTS.append(
        r"(?:" + "|".join(re.escape(h) for h in _INTERNAL_HOSTS) + r")\.rugmunch\.io\b"
    )
_INTERNAL_FQDN_PARTS.append(r"(?:[a-z0-9-]+\.)+(?:internal|local)\b")
_INTERNAL_FQDN_RE = re.compile(
    r"\b(?:" + "|".join(_INTERNAL_FQDN_PARTS) + r")",
    re.IGNORECASE,
)
# Bare host labels that are unique enough to redact without qualification.
_BARE_HOST_RE = (
    re.compile(
        r"\b(?:" + "|".join(re.escape(h) for h in _INTERNAL_HOSTS) + r")\b",
        re.IGNORECASE,
    )
    if _INTERNAL_HOSTS
    else None
)
# RFC1918 + CGNAT (100.64/10) private IPs.
_PRIVATE_IP_RE = re.compile(
    r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2[0-9]|3[01])|100\.(?:6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7]))"
    r"(?:\.\d{1,3}){2,3}\b"
)

_REDACTED = "[REDACTED]"


def redact_output(text: str) -> str:
    """Strip obvious secret patterns from *text* (fail-closed).

    Redacts EVM private keys, labeled 64-hex keys, 12/24-word mnemonic
    phrases, API-key-looking strings, internal hostnames, and private IPs.
    Pure string transform — never raises; non-str input is coerced.
    """
    from rugbrain import guardrails as _pkg

    if not _pkg._GUARDRAILS_ENABLED:
        return text
    out = text
    out = _EVM_KEY_RE.sub(_REDACTED, out)
    out = _LABELED_HEX_KEY_RE.sub(_REDACTED, out)
    out = _pkg._redact_mnemonics(out)
    out = _API_KEY_RE.sub(_REDACTED, out)
    out = _GENERIC_KEY_RE.sub(_REDACTED, out)
    out = _BEARER_TOKEN_RE.sub(_REDACTED, out)
    out = _INTERNAL_FQDN_RE.sub(_REDACTED, out)
    if _BARE_HOST_RE is not None:
        out = _BARE_HOST_RE.sub(_REDACTED, out)
    out = _PRIVATE_IP_RE.sub(_REDACTED, out)
    return out
