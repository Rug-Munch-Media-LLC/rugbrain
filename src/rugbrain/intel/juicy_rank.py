"""Scoring primitives for the materialised "juicy content" ranking.

Pure scoring only: no network, no clock reads. The nightly builder
(``scripts/rank_juicy_content.py``) and the served read
(``GET /api/v1/intel/ranking``) both import this module, so what a caller reads
is scored by the same code that wrote it — one implementation, one truth.

Weights are a *seam*, not a constant: :func:`load_weights` reads a private
override (``RUGBRAIN_JUICY_WEIGHTS`` → path to a JSON file, or inline JSON) and
falls back to :data:`BASELINE_WEIGHTS`, an equal-weight shape with no tuned
numbers. The resolved values travel in the emitted header, so a ranking can
always be explained after the fact. This file stays free of tuning.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
HALF_LIFE_DAYS = 180.0
WEIGHTS_ENV = "RUGBRAIN_JUICY_WEIGHTS"
#: Documented, untuned shape (equal weight). Tuned numbers live in the private
#: weights file, never in this repository.
#: Keys are the published SHAPE; the numbers are a private seam
#: ($RUGBRAIN_JUICY_WEIGHTS -> JSON file or inline JSON). Absent the seam every
#: key carries the same neutral share, so no tuned method ships in this repo.
WEIGHT_KEYS: tuple[str, ...] = ("recency", "severity", "corroboration", "relevance")
SEVERITY_KEYS: tuple[str, ...] = ("critical", "high", "medium", "low", "info", "unknown")


def _neutral(keys: tuple[str, ...]) -> dict[str, float]:
    """Equal share per key: the shape without the weights."""
    return dict.fromkeys(keys, 1.0 / len(keys))


BASELINE_WEIGHTS: dict[str, float] = _neutral(WEIGHT_KEYS)
SEVERITY_WEIGHT: dict[str, float] = _neutral(SEVERITY_KEYS)
JUICY_TERMS: tuple[str, ...] = (
    "exploit",
    "hack",
    "scam",
    "rug",
    "zero-day",
    "zeroday",
    "drain",
    "theft",
    "phish",
    "vulnerab",
    "advisory",
    "detection",
    "playbook",
    "tailscale",
    "incident",
    "outdated",
)

_PATH_RE = re.compile(r"(?:/srv|/root|/home/dev)/[^\s\"'`)]+")
_IP_RE = re.compile(r"\b(?:10|100|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_SECRET_RE = re.compile(r"\b(?:sk|pk|api|key)[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


def redact(value: str) -> str:
    """Strip host paths, internal addresses and secret-shaped tokens."""
    out = _PATH_RE.sub("[redacted path]", str(value))
    out = _IP_RE.sub("[internal address]", out)
    return _SECRET_RE.sub("[redacted credential]", out)


def parse_timestamp(value: Any) -> float | None:
    """Epoch seconds from an ISO string or an epoch number; ``None`` if unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def recency_score(timestamp: float | None, now_ts: float) -> float:
    """Exponential decay with :data:`HALF_LIFE_DAYS`; undated items are discounted, not dropped."""
    if timestamp is None:
        return 0.3
    age_days = max(0.0, (now_ts - timestamp) / 86400.0)
    return float(0.5 ** (age_days / HALF_LIFE_DAYS))


def severity_score(severity: Any) -> float:
    return SEVERITY_WEIGHT.get(str(severity or "").strip().lower(), SEVERITY_WEIGHT["unknown"])


def relevance_score(text: str) -> float:
    """How squarely an item sits in the exploit/hack/scam/zero-day family."""
    haystack = str(text).lower()
    hits = sum(1 for term in JUICY_TERMS if term in haystack)
    return min(1.0, 0.45 + 0.15 * hits)


def cluster_key(item: dict[str, Any]) -> str:
    """Cheap corroboration key: the first subject tokens, normalised.

    Two collections reporting the same protocol land on the same key, which is
    the only signal available without an entity resolver in the hot path.
    """
    subject = str(item.get("title") or item.get("name") or item.get("id") or "")
    tokens = re.findall(r"[a-z0-9]+", subject.lower())
    return hashlib.sha256(" ".join(tokens[:6]).encode()).hexdigest()[:16]


def load_weights(source: str | None = None) -> tuple[dict[str, float], str]:
    """Resolve scoring weights: private override when present, else the baseline.

    ``source`` (default ``$RUGBRAIN_JUICY_WEIGHTS``) is either a path to a JSON
    file holding the four weight names or an inline JSON object. The returned
    label is recorded in the ranking header so a score can always be explained.
    """
    ref = (source if source is not None else os.getenv(WEIGHTS_ENV, "")).strip()
    if not ref:
        return dict(BASELINE_WEIGHTS), "baseline"
    label = "inline" if ref.startswith("{") else Path(ref).name
    try:
        raw = json.loads(ref if ref.startswith("{") else Path(ref).read_text(encoding="utf-8"))
        weights = {name: float(raw[name]) for name in BASELINE_WEIGHTS}
    except (OSError, ValueError, TypeError, KeyError):
        return dict(BASELINE_WEIGHTS), f"baseline (invalid {label})"
    total = sum(weights.values())
    if set(raw) != set(BASELINE_WEIGHTS) or total <= 0 or min(weights.values()) < 0:
        return dict(BASELINE_WEIGHTS), f"baseline (invalid {label})"
    return {name: round(value / total, 6) for name, value in weights.items()}, f"private:{label}"


def base_scores(item: dict[str, Any], now_ts: float) -> dict[str, float]:
    """Recency / severity / relevance for one item (corroboration needs the cluster)."""
    tags = item.get("tags") or []
    text = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("kind", "")),
            " ".join(str(tag) for tag in tags),
        ]
    )
    return {
        "recency": round(recency_score(item.get("timestamp"), now_ts), 4),
        "severity": severity_score(item.get("severity")),
        "relevance": round(relevance_score(text), 4),
    }


def rank_items(
    items: list[dict[str, Any]],
    now_ts: float,
    limit: int = 200,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Score, corroborate and order items; returns the top ``limit``."""
    weights = weights or BASELINE_WEIGHTS
    scored: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in items:
        key = (str(raw.get("collection", "")), str(raw.get("id", "")))
        if key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        item["scores"] = base_scores(item, now_ts)
        item["cluster"] = cluster_key(item)
        scored.append(item)

    clusters: dict[str, set[str]] = {}
    for item in scored:
        clusters.setdefault(item["cluster"], set()).add(str(item.get("collection", "")))

    for item in scored:
        sources = clusters[item["cluster"]]
        scores = item["scores"]
        scores["corroboration"] = round(min(1.0, len(sources) / 3.0), 4)
        item["source_count"] = len(sources)
        item["score"] = round(sum(weights[name] * scores[name] for name in weights), 4)

    scored.sort(key=lambda entry: (entry["score"], entry.get("timestamp") or 0.0), reverse=True)
    return scored[:limit]
