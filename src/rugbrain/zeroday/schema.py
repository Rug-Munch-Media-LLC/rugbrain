"""One normalized record shape for 0-day, exploit, bounty and contest intel.

Every adapter in ``sources_vuln`` / ``sources_exploit`` / ``sources_bounty``
emits :class:`Record`; nothing downstream needs the upstream schema.

Parsers leave a field ``None`` when the source does not state it: loss, reward,
chain, tx and scope are never inferred. That is what keeps the feed honest.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

KINDS = ("0day", "exploit", "bounty", "contest")
SEVERITIES = ("critical", "high", "medium", "low", "info", "unknown")
SEVERITY_LEVEL = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "unknown": -1}
CONFIDENCES = ("confirmed", "likely", "unconfirmed")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.I)


def slugify(text: str) -> str:
    """Lowercase ASCII slug used inside alias keys."""
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")


def parse_ts(value: Any) -> datetime | None:
    """Parse ISO-8601 / epoch seconds into an aware UTC datetime, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_ts(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso_day(value: Any) -> str:
    """``YYYY-MM-DD`` in UTC, or ``""`` when unparseable."""
    dt = parse_ts(value)
    return dt.date().isoformat() if dt else ""


def iso_utc(value: Any) -> str | None:
    """Normalized ISO-8601 UTC timestamp, or None."""
    dt = parse_ts(value)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def severity_from_cvss(score: Any) -> str:
    """CVSS v3/v4 base score → severity band."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 9.0:
        return "critical"
    if value >= 7.0:
        return "high"
    if value >= 4.0:
        return "medium"
    if value > 0:
        return "low"
    return "info"


def severity_from_text(text: Any) -> str:
    """Source severity label (``MODERATE``, ``Critical``, ``9.8`` …) → band."""
    raw = str(text or "").strip().lower()
    if not raw:
        return "unknown"
    if raw.replace(".", "", 1).isdigit():
        return severity_from_cvss(raw)
    for word in ("critical", "high", "medium", "moderate", "low"):
        if word in raw:
            return "medium" if word == "moderate" else word
    return "unknown"


@dataclass(frozen=True, slots=True)
class Record:
    """One normalized intel item, whatever the upstream source looked like."""

    source: str
    kind: str
    title: str
    url: str
    published_at: str | None = None
    severity: str = "unknown"
    chain: str | None = None
    protocol: str | None = None
    loss_usd: float | None = None
    tx_ref: str | None = None
    address: str | None = None
    affected_versions: tuple[str, ...] = ()
    fixed_version: str | None = None
    poc_url: str | None = None
    reward_usd: float | None = None
    scope: str | None = None
    confidence: str = "unconfirmed"
    citations: tuple[str, ...] = ()
    external_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        kind = self.kind.strip().lower()
        severity = self.severity.strip().lower()
        confidence = self.confidence.strip().lower()
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {self.kind!r}")
        if severity not in SEVERITIES:
            severity = severity_from_text(severity)
        if confidence not in CONFIDENCES:
            raise ValueError(f"unknown confidence: {self.confidence!r}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "affected_versions", tuple(self.affected_versions))
        object.__setattr__(self, "citations", tuple(self.citations))
        object.__setattr__(
            self, "external_ids", tuple(i.strip().upper() for i in self.external_ids)
        )

    def alias_keys(self) -> tuple[str, ...]:
        """Every identity this record can be deduped on (most specific first)."""
        keys: list[str] = []
        if self.tx_ref:
            keys.append(f"tx:{self.tx_ref.strip().lower()}")
        for external in self.external_ids:
            if _CVE_RE.match(external):
                keys.append(f"cve:{external.upper()}")
        for external in self.external_ids:
            if external.upper().startswith("GHSA-"):
                keys.append(f"adv:{external.upper()}")
        if self.protocol and self.published_at and self.kind in ("0day", "exploit"):
            keys.append(f"incident:{slugify(self.protocol)}:{iso_day(self.published_at)}")
        if self.kind in ("bounty", "contest") and self.url:
            keys.append(f"program:{slugify(self.url.rstrip('/').rsplit('/', 1)[-1])}")
        keys.append(f"url:{hashlib.sha256(self.url.encode()).hexdigest()[:16]}")
        return tuple(dict.fromkeys(k for k in keys if k))

    def canonical_key(self) -> str:
        """Primary cluster key — the most specific identity available."""
        return self.alias_keys()[0]

    def fingerprint(self) -> str:
        """Hash of mutable content; a change means the feed emits ``updated``."""
        payload = {
            "title": self.title,
            "severity": self.severity,
            "loss_usd": self.loss_usd,
            "reward_usd": self.reward_usd,
            "fixed_version": self.fixed_version,
            "affected_versions": list(self.affected_versions),
            "scope": self.scope,
            "confidence": self.confidence,
            "citations": list(self.citations),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe view (adds the identity keys the store needs)."""
        data: dict[str, Any] = asdict(self)
        data["id"] = self.canonical_key()
        data["fingerprint"] = self.fingerprint()
        return data


__all__ = [
    "CONFIDENCES",
    "KINDS",
    "SEVERITIES",
    "SEVERITY_LEVEL",
    "Record",
    "iso_day",
    "iso_utc",
    "parse_ts",
    "severity_from_cvss",
    "severity_from_text",
    "slugify",
]
