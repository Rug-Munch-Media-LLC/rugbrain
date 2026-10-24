"""Dataset → registry row extractors for ``config/x_registry.json``.

Every handle comes from an existing, REAL dataset (no invented accounts):

* ``data/x_sources.json``          — verification-passed inventory build
* ``data/x_sources_rejected.json`` — rejected probes: impersonations + squats
* ``config/x_sources_seed.json``   — canonical ``renames`` map (2026-10-23)

Handles are canonicalized through the seed's ``renames`` map, so a renamed
account never ships under its dead name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "x_registry.json"
TARGET_SIZE = 500
KINDS = ("news", "kol", "scam_caller", "security", "analyst")

KIND_OF_CATEGORY: dict[str, str] = {
    "news_primary": "news",
    "research": "analyst",
    "aggregator": "analyst",
    "security": "security",
    "kol": "kol",
    "ecosystem": "kol",  # founder/official project voices; ``role`` keeps the nuance
}
ROLE_OF_CATEGORY: dict[str, str] = {
    "news_primary": "news_desk",
    "research": "analyst",
    "aggregator": "aggregator",
    "security": "security_researcher",
    "kol": "kol",
    "ecosystem": "founder",
}
#: Rejection reasons that mark the account itself as negative signal.
NEGATIVE_REASONS: tuple[str, ...] = ("name_mismatch_impersonation", "squat_probe_hit")
CHAIN_WORDS = (
    "solana",
    "ethereum",
    "btc",
    "bitcoin",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "ton",
    "sui",
    "aptos",
    "near",
    "celestia",
    "blast",
    "mantle",
)


def _read(path: Path) -> Any:
    """Parsed JSON, or None when the dataset is absent (fail-open)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _norm(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lstrip()


def seed_renames() -> dict[str, str]:
    """``old_handle -> new_handle`` from the seed (lowercased keys)."""
    data = _read(ROOT / "config" / "x_sources_seed.json") or {}
    renames = data.get("renames") if isinstance(data, dict) else None
    return {str(k).lower(): str(v) for k, v in (renames or {}).items() if v}


def canonical(handle: Any, renames: dict[str, str] | None = None) -> str:
    """Canonical handle: normalized, then seed-renamed when stale."""
    name = _norm(handle)
    return (renames if renames is not None else seed_renames()).get(name.lower()) or name


def _chain_of(entry: dict[str, Any]) -> str:
    blob = " ".join(str(entry.get(k) or "") for k in ("handle", "name", "site", "site_url")).lower()
    for word in CHAIN_WORDS:
        if word in blob:
            return "bitcoin" if word in ("btc", "bitcoin") else word
    return ""


def _row(handle: str, name: Any, kind: str, role: str, **extra: Any) -> dict[str, Any]:
    return {"handle": handle, "name": name or handle, "kind": kind, "role": role, **extra}


def from_verified(renames: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Verification-passed inventory entries → registry rows."""
    data = _read(ROOT / "data" / "x_sources.json") or {}
    entries = data.get("sources") or data.get("entries") or []
    names = renames if renames is not None else seed_renames()
    rows: list[dict[str, Any]] = []
    for entry in entries:
        raw = _norm(entry.get("handle"))
        kind = KIND_OF_CATEGORY.get(str(entry.get("category") or ""))
        if not raw or kind is None:
            continue
        handle = canonical(raw, names)
        provenance = list(entry.get("provenance") or ["data/x_sources.json"])
        evidence_url = str(entry.get("evidence_url") or f"https://x.com/{handle}")
        if handle != raw:
            provenance.append(f"config/x_sources_seed.json renames: {raw} -> {handle}")
            if evidence_url.rstrip("/").lower().endswith("/" + raw.lower()):
                evidence_url = f"https://x.com/{handle}"
        rows.append(
            _row(
                handle,
                entry.get("name"),
                kind,
                ROLE_OF_CATEGORY[str(entry["category"])],
                category=str(entry.get("category")),
                chain_focus=_chain_of(entry),
                followers=entry.get("followers") or 0,
                trust=entry.get("trust", 0.0),
                source="x_sources_inventory",
                provenance=provenance,
                evidence_url=evidence_url,
                evidence=(
                    f"inventory-verified {str(entry.get('verified_at') or '')[:10]} "
                    f"({entry.get('followers', 0)} followers)"
                ),
                last_seen=str(entry.get("verified_at") or data.get("generated_at", ""))[:10],
            )
        )
    return rows


def rejected_negative(renames: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Impersonations + squat probes: negative-signal rows (trust 0, never ingested)."""
    data = _read(ROOT / "data" / "x_sources_rejected.json") or {}
    names = renames if renames is not None else seed_renames()
    rows: list[dict[str, Any]] = []
    for entry in data.get("rejected", []):
        reason = str(entry.get("reason") or "")
        # The squat/impersonator is the OBSERVED handle; ``handle`` is the entity mimicked.
        handle = canonical(entry.get("observed_handle") or entry.get("handle"), names)
        if reason not in NEGATIVE_REASONS or not handle:
            continue
        rows.append(
            _row(
                handle,
                entry.get("observed_name"),
                "scam_caller",
                "impersonator" if reason == "name_mismatch_impersonation" else "squatter",
                category="impersonation",
                chain_focus="",
                followers=entry.get("followers") or 0,
                trust=0.0,
                source="x_sources_rejected",
                provenance=[
                    *(entry.get("provenance") or ["data/x_sources_rejected.json"]),
                    f"rejected: {reason}",
                ],
                evidence_url=entry.get("evidence_url") or f"https://x.com/{handle}",
                evidence=(
                    f"{reason}: mimics @{_norm(entry.get('handle'))}"
                    f" ({entry.get('observed_name') or 'no name'}, "
                    f"{entry.get('followers', 0)} followers)"
                ),
                last_seen="2026-10-22",
            )
        )
    return rows


__all__ = [
    "KINDS",
    "KIND_OF_CATEGORY",
    "NEGATIVE_REASONS",
    "REGISTRY",
    "ROLE_OF_CATEGORY",
    "ROOT",
    "TARGET_SIZE",
    "canonical",
    "from_verified",
    "rejected_negative",
    "seed_renames",
]
