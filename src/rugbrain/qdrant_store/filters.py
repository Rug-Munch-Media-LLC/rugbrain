"""Payload filter construction for Qdrant requests."""

from __future__ import annotations

from typing import Any

# ── Payload filter helpers ─────────────────────────────────────────────

# Canonical filterable payload fields (see rugbrain.crypto_schema). Keeping
# the vocabulary here lets /api/v1/search validate user input before it hits
# Qdrant, and keeps crypto_schema free of Qdrant-specific concerns.
FILTERABLE_MATCH_FIELDS = (
    "severity",
    "chain",
    "attack_vector",
    "category",
    "protocol",
    "source_type",
    "collection",
    "scam_type",
)
# Range filters: payload field → Qdrant range keys allowed.
FILTERABLE_RANGE_FIELDS = (
    ("date_ts", ("gte", "lte")),
    ("amount_lost_usd", ("gte", "lte")),
)


def build_qdrant_filter(
    match: dict[str, str] | None = None,
    ranges: list[tuple[str, dict[str, float]]] | None = None,
) -> dict[str, Any] | None:
    """Build a Qdrant payload filter from exact-match and range constraints.

    ``match`` maps payload field → exact value (keyword match). ``ranges`` is
    a list of ``(field, {"gte": ..., "lte": ...})`` pairs. Returns ``None``
    when there is nothing to filter on (callers then omit the filter entirely).
    """
    must: list[dict[str, Any]] = []
    for key, value in (match or {}).items():
        if key and value is not None and str(value).strip():
            must.append({"key": key, "match": {"value": str(value)}})
    for key, constraint in ranges or []:
        if not constraint:
            continue
        if key and any(constraint.get(op) is not None for op in ("gte", "lte")):
            range_filter: dict[str, Any] = {}
            if constraint.get("gte") is not None:
                range_filter["gte"] = constraint["gte"]
            if constraint.get("lte") is not None:
                range_filter["lte"] = constraint["lte"]
            must.append({"key": key, "range": range_filter})
    if not must:
        return None
    return {"must": must}


def build_search_filters(
    severity: str | None = None,
    chain: str | None = None,
    attack_vector: str | None = None,
    category: str | None = None,
    protocol: str | None = None,
    source_type: str | None = None,
    collection: str | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> dict[str, Any] | None:
    """Build a structured filter dict for ``hybrid_search`` from API params.

    Mirrors ``build_qdrant_filter`` output shape (``{"match": ..., "range": ...}``)
    but tolerates unparsed date strings and unknown fields, so endpoints can
    pass user input straight through. Date strings are converted to unix
    timestamps for the ``date_ts`` range field.
    """
    from rugbrain.crypto_schema import date_to_ts, normalize_severity

    match: dict[str, str] = {}
    if severity:
        match["severity"] = normalize_severity(severity)
    if chain:
        match["chain"] = str(chain).strip().lower()
    if attack_vector:
        match["attack_vector"] = str(attack_vector).strip().lower()
    if category:
        match["category"] = str(category).strip()
    if protocol:
        match["protocol"] = str(protocol).strip()
    if source_type:
        match["source_type"] = str(source_type).strip().lower()
    if collection:
        match["collection"] = str(collection).strip()

    ranges: list[tuple[str, dict[str, float]]] = []
    if date_after:
        ts = date_to_ts(date_after)
        if ts is not None:
            ranges.append(("date_ts", {"gte": ts}))
    if date_before:
        ts = date_to_ts(date_before)
        if ts is not None:
            ranges.append(("date_ts", {"lte": ts}))
    if amount_min is not None:
        ranges.append(("amount_lost_usd", {"gte": float(amount_min)}))
    if amount_max is not None:
        ranges.append(("amount_lost_usd", {"lte": float(amount_max)}))

    if not match and not ranges:
        return None
    return {"match": match, "range": ranges}


def apply_payload_filter(payload: dict[str, Any], qdrant_filter: dict[str, Any] | None) -> None:
    """Inline-add ``filter`` to a Qdrant request payload when given."""
    if qdrant_filter:
        payload["filter"] = qdrant_filter


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a caller filter dict to a ready-to-send Qdrant filter.

    Accepts three shapes and returns None when there is nothing to filter on:
      - an already-built Qdrant filter (has a ``must`` key) → passed through
      - a structured ``{"match": {...}, "range": [...]}`` dict
      - a plain ``{field: value}`` exact-match dict (legacy form)
    """
    if not filters:
        return None
    if "must" in filters:
        return filters
    if "match" in filters or "range" in filters:
        return build_qdrant_filter(filters.get("match"), filters.get("range"))
    return build_qdrant_filter(filters, [])
