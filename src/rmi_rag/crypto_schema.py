"""Canonical crypto-security metadata schema for the RAG knowledge engine.

Every document stored in Qdrant is enriched to a single normalized shape so
that hybrid search can filter uniformly across collections (``threat_intel``,
``scam_intel``, ``hacks``, ``news``, ``wallet``). The canonical payload fields
are:

    attack_vector   — reentrancy, flash-loan, oracle-manipulation, ...
    severity        — critical | high | medium | low | info | unknown
    chain           — ethereum, bsc, solana, multi, ...
    amount_lost_usd — float (0.0 when unknown)
    date            — ISO date (YYYY-MM-DD) or "" when unknown
    protocol        — affected protocol / project name
    source_type     — rekt_news, defihacklabs, swc_registry, seed, ...

These live at the top level of the Qdrant payload (cheap to filter) and are
also mirrored inside ``metadata`` for backward compatibility with consumers
that read ``metadata.<field>``.

Pure functions only — no I/O — so the helpers are unit-testable and shared by
``scripts/enrich_metadata.py``, ``scripts/ingest_swc_registry.py`` and
``scripts/crypto_alert_digest.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# ── Controlled vocabulary ─────────────────────────────────────────────

SEVERITIES = ("critical", "high", "medium", "low", "info", "unknown")

ATTACK_VECTORS = (
    "reentrancy",
    "flash-loan",
    "oracle-manipulation",
    "access-control",
    "bridge-exploit",
    "governance-attack",
    "signature-malleability",
    "integer-overflow",
    "key-compromise",
    "rug-pull",
    "honeypot",
    "phishing",
    "withdrawal-exploit",
    "denial-of-service",
    "front-running",
    "supply-chain",
    "input-validation",
    "smart-contract-exploit",
    "unknown",
)

CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum", "eth ", "erc-20", "erc-721", "evm", "mainnet"),
    "bsc": ("bsc", "binance smart chain", "bnb chain", "bep-20"),
    "solana": ("solana", "spl token", "spl-token"),
    "polygon": ("polygon", "matic"),
    "arbitrum": ("arbitrum", "arb "),
    "optimism": ("optimism", "op mainnet"),
    "base": (" base ", "base network"),
    "avalanche": ("avalanche", "avax"),
    "tron": ("tron", "trc-20"),
    "fantom": ("fantom", " ftm "),
    "multi": ("multi-chain", "cross-chain", "multiple chains"),
}

# SWC ID → canonical attack vector (derived from the SWC Registry titles,
# see https://github.com/SmartContractSecurity/SWC-registry).
SWC_VECTORS: dict[str, str] = {
    "SWC-100": "access-control",
    "SWC-101": "integer-overflow",
    "SWC-102": "supply-chain",
    "SWC-103": "supply-chain",
    "SWC-104": "input-validation",
    "SWC-105": "access-control",
    "SWC-106": "access-control",
    "SWC-107": "reentrancy",
    "SWC-108": "access-control",
    "SWC-109": "smart-contract-exploit",
    "SWC-110": "input-validation",
    "SWC-111": "supply-chain",
    "SWC-112": "access-control",
    "SWC-113": "denial-of-service",
    "SWC-114": "front-running",
    "SWC-115": "access-control",
    "SWC-116": "oracle-manipulation",
    "SWC-117": "signature-malleability",
    "SWC-118": "access-control",
    "SWC-119": "smart-contract-exploit",
    "SWC-120": "oracle-manipulation",
    "SWC-121": "signature-malleability",
    "SWC-122": "smart-contract-exploit",
    "SWC-123": "input-validation",
    "SWC-124": "smart-contract-exploit",
    "SWC-125": "smart-contract-exploit",
    "SWC-126": "denial-of-service",
    "SWC-127": "smart-contract-exploit",
    "SWC-128": "denial-of-service",
    "SWC-129": "smart-contract-exploit",
    "SWC-130": "input-validation",
    "SWC-131": "smart-contract-exploit",
    "SWC-132": "smart-contract-exploit",
    "SWC-133": "signature-malleability",
    "SWC-134": "smart-contract-exploit",
    "SWC-135": "smart-contract-exploit",
    "SWC-136": "supply-chain",
}

# SWC ID → default severity when the weakness is exploitable.
SWC_SEVERITY: dict[str, str] = {
    "SWC-100": "high",
    "SWC-101": "high",
    "SWC-102": "low",
    "SWC-103": "low",
    "SWC-104": "high",
    "SWC-105": "critical",
    "SWC-106": "critical",
    "SWC-107": "critical",
    "SWC-108": "high",
    "SWC-109": "high",
    "SWC-110": "medium",
    "SWC-111": "low",
    "SWC-112": "critical",
    "SWC-113": "medium",
    "SWC-114": "medium",
    "SWC-115": "high",
    "SWC-116": "medium",
    "SWC-117": "high",
    "SWC-118": "medium",
    "SWC-119": "high",
    "SWC-120": "high",
    "SWC-121": "high",
    "SWC-122": "high",
    "SWC-123": "medium",
    "SWC-124": "critical",
    "SWC-125": "medium",
    "SWC-126": "medium",
    "SWC-127": "high",
    "SWC-128": "medium",
    "SWC-129": "low",
    "SWC-130": "high",
    "SWC-131": "low",
    "SWC-132": "low",
    "SWC-133": "medium",
    "SWC-134": "medium",
    "SWC-135": "low",
    "SWC-136": "low",
}

# Free-text keyword patterns → canonical attack vector.
_VECTOR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"re-?entr(anc)?y", "reentrancy"),
    (r"flash ?loan", "flash-loan"),
    (r"oracle|price ?manipulation|price feed|oracle man", "oracle-manipulation"),
    (r"access ?control|visibility|selfdestruct|delegatecall|tx\.origin|owner", "access-control"),
    (r"bridge", "bridge-exploit"),
    (r"governance", "governance-attack"),
    (r"signature malleab|signature replay", "signature-malleability"),
    (r"overflow|underflow|arithmetic", "integer-overflow"),
    (r"private ?key|stolen key|key compromise|seed phrase", "key-compromise"),
    (r"rug ?pull|liquidity removed|lp removed|exit scam", "rug-pull"),
    (r"honeypot", "honeypot"),
    (r"phish|fake site|malicious site|approval phish", "phishing"),
    (r"withdraw", "withdrawal-exploit"),
    (r"denial of service|griefing|gas limit|dos ", "denial-of-service"),
    (r"front.?run|sandwich|transaction order|mev", "front-running"),
    (r"outdated compiler|floating pragma|deprecated function", "supply-chain"),
    (r"assert violation|array index|input validation|unchecked", "input-validation"),
    (r"exploit|vulnerab|bug|hack", "smart-contract-exploit"),
)

_SEVERITY_SYNONYMS: dict[str, str] = {
    "critical": "critical",
    "severe": "critical",
    "critical/high": "critical",
    "major": "high",
    "high": "high",
    "medium": "medium",
    "moderate": "medium",
    "low": "low",
    "info": "info",
    "informational": "info",
    "informative": "info",
    "unknown": "unknown",
    "": "unknown",
}

_AMOUNT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([BbMmKk]?)")

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MD_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_WORD_DATE_RE = re.compile(
    r"(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})", re.IGNORECASE
)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


# ── Attack vector ──────────────────────────────────────────────────────


SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
    "unknown": -1,
}


def escalate_severity(current: str, amount_usd: float) -> str:
    """Return the more severe of *current* and the amount-implied severity.

    Monotonic upgrade only — never downgrades. Used because ingesters
    (e.g. rekt_news) often hardcode ``high`` regardless of impact; a $197M
    hack should be ``critical``, not ``high``.
    """
    if amount_usd <= 0:
        return current
    implied = infer_severity_from_amount(amount_usd)
    if SEVERITY_RANK.get(implied, -1) > SEVERITY_RANK.get(current, -1):
        return implied
    return current


def detect_attack_vector(text: str, fallback: str = "unknown") -> str:
    """Map free text to a canonical attack vector (first keyword match wins)."""
    if not text:
        return fallback
    lowered = text.lower()
    for pattern, vector in _VECTOR_PATTERNS:
        if re.search(pattern, lowered):
            return vector
    return fallback


# ── Severity ───────────────────────────────────────────────────────────


def normalize_severity(value: Any) -> str:
    """Normalize a free-form severity value to the canonical vocabulary."""
    if value is None:
        return "unknown"
    return _SEVERITY_SYNONYMS.get(str(value).strip().lower(), "unknown")


def infer_severity_from_amount(amount_usd: float) -> str:
    """Heuristic severity based on reported USD loss."""
    if amount_usd >= 10_000_000:
        return "critical"
    if amount_usd >= 1_000_000:
        return "high"
    if amount_usd >= 100_000:
        return "medium"
    if amount_usd > 0:
        return "low"
    return "unknown"


# ── Chain ──────────────────────────────────────────────────────────────


def detect_chain(text: str, fallback: str = "unknown") -> str:
    """Detect chain(s) mentioned in free text; ``multi`` when several appear.

    ``0x`` hex addresses are EVM — labeled ``ethereum`` unless another chain
    is explicitly named.
    """
    if not text:
        return fallback
    lowered = f" {text.lower()} "
    found: set[str] = set()
    for chain, aliases in CHAIN_ALIASES.items():
        if chain in ("multi",):
            continue
        for alias in aliases:
            if alias in lowered:
                found.add(chain)
                break
    if not found and re.search(r"\b0x[a-fA-F0-9]{8,}\b", text):
        found.add("ethereum")
    if len(found) > 1:
        return "multi"
    if len(found) == 1:
        return found.pop()
    return fallback


# ── Amount ─────────────────────────────────────────────────────────────


def parse_amount_usd(value: Any, text: str = "") -> float:
    """Extract a USD amount from a numeric field or ``$X.XXM/B/K`` text."""
    if value is not None:
        try:
            amount = float(value)
            if amount > 0:
                return amount
        except (TypeError, ValueError):
            pass
    if not text:
        return 0.0
    m = _AMOUNT_RE.search(text)
    if not m:
        return 0.0
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    suffix = m.group(2).lower()
    multiplier = {"b": 1_000_000_000, "m": 1_000_000, "k": 1_000}.get(suffix, 1)
    return round(number * multiplier, 2)


# ── Date ───────────────────────────────────────────────────────────────


def parse_date(value: Any, text: str = "") -> str:
    """Normalize a date to ISO ``YYYY-MM-DD`` (best effort, else ``""``)."""
    raw = str(value or "").strip()
    for source in (raw, text):
        if not source:
            continue
        m = _ISO_DATE_RE.search(source)
        if m:
            try:
                return (
                    datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
                )
            except ValueError:
                pass
        m = _MD_DATE_RE.search(source)
        if m:
            try:
                return (
                    datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date().isoformat()
                )
            except ValueError:
                pass
        m = _WORD_DATE_RE.search(source)
        if m:
            try:
                month = _MONTHS[m.group(2).lower()]
                return datetime(int(m.group(3)), month, int(m.group(1))).date().isoformat()
            except ValueError:
                pass
    # Year-only fallback → Jan 1 (conservative lower bound for range filters).
    if raw:
        m = _YEAR_RE.search(raw)
        if m:
            return f"{m.group(1)}-01-01"
    return ""


def date_to_ts(date_iso: str) -> float | None:
    """Convert an ISO date to a unix timestamp (UTC) for range filters."""
    if not date_iso:
        return None
    try:
        dt = datetime.fromisoformat(date_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return None


# ── Source type ────────────────────────────────────────────────────────


def normalize_source_type(source: Any) -> str:
    """Map a free-form source string to the canonical source_type enum."""
    s = str(source or "").strip().lower()
    if not s:
        return "unknown"
    if s.startswith("rekt"):
        return "rekt_news"
    if s.startswith("defihacklabs"):
        return "defihacklabs"
    if s.startswith("swc"):
        return "swc_registry"
    if s.startswith("scamsniffer"):
        return "scamsniffer"
    if s == "seed":
        return "seed"
    if s.startswith("redis"):
        return "wallet_label"
    if s.startswith("news"):
        return "news"
    if s.startswith("goplus"):
        return "goplus"
    if s.startswith("alert"):
        return "alert_digest"
    return s if len(s) <= 32 else "other"


# ── Canonical enrichment ───────────────────────────────────────────────

CANONICAL_FIELDS = (
    "attack_vector",
    "severity",
    "chain",
    "amount_lost_usd",
    "date",
    "protocol",
    "source_type",
)


def enrich_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* with canonical fields derived and filled.

    Reads from top-level payload keys first, then ``metadata``, then derives
    from ``content``. Canonical fields are written at the top level and
    mirrored into ``metadata``. Never overwrites an existing canonical value
    unless the source value is empty.
    """
    out = dict(payload)
    metadata = out.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    content = str(out.get("content", "") or "")
    source = out.get("source", "") or metadata.get("source", "")

    def _first(*paths: tuple[str, ...]) -> Any:
        for path in paths:
            for container in (out, metadata):
                value = container
                ok = True
                for key in path:
                    if not isinstance(value, dict) or key not in value:
                        ok = False
                        break
                    value = value[key]
                if ok and value is not None and str(value).strip():
                    return value
        return None

    # attack_vector — only trust fields that name a known vector; ``category``
    # values like "historical"/"smart-contract" are labels, not vectors.
    vector = _first(("attack_vector",), ("scam_type",), ("attack_type",))
    if vector:
        candidate = str(vector).strip().lower().replace("_", "-")
        vector = candidate if candidate in ATTACK_VECTORS else None
    if not vector:
        vector = detect_attack_vector(content)
    # severity
    severity = _first(("severity",))
    amount = _first(("amount_lost_usd",), ("amount_usd",), ("amount_lost",))
    amount_f = parse_amount_usd(amount, content)
    severity_norm = normalize_severity(severity)
    if severity_norm == "unknown" and amount_f > 0:
        severity_norm = infer_severity_from_amount(amount_f)
    else:
        severity_norm = escalate_severity(severity_norm, amount_f)
    # chain
    chain = _first(("chain",))
    if not chain or str(chain).strip() in ("", "multi", "unknown"):
        detected = detect_chain(content, fallback=str(chain or "unknown") or "unknown")
        chain = detected
    # amount_lost_usd
    # date
    date_raw = _first(
        ("date",), ("hack_date",), ("published_at",), ("published",), ("year",), ("attack_date",)
    )
    date_iso = parse_date(date_raw, content)
    # protocol
    protocol = _first(("protocol",), ("contract_name",), ("title",))
    # source_type
    source_type = _first(("source_type",))
    source_type = normalize_source_type(source_type if source_type else source)

    canonical = {
        "attack_vector": str(vector or "unknown"),
        "severity": severity_norm,
        "chain": str(chain or "unknown"),
        "amount_lost_usd": amount_f,
        "date": date_iso,
        "protocol": str(protocol or ""),
        "source_type": source_type,
    }
    for key, value in canonical.items():
        out[key] = value
        metadata[key] = value
    if date_iso:
        ts = date_to_ts(date_iso)
        if ts is not None:
            out["date_ts"] = ts
            metadata["date_ts"] = ts
    # mirror category for convenience (used by existing consumers)
    category = _first(("category",))
    if category:
        out["category"] = str(category)
    out["metadata"] = metadata
    return out
