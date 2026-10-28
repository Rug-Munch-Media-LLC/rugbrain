"""``GET /api/v1/intel/ranking`` — read the materialised juicy-content ranking.

The ranking is built nightly by ``scripts/rank_juicy_content.py`` and read from
a JSON file here; a request never re-scores the corpus (that would be a
per-request recompute over ~400k points). The file carries its own header
(weights, half-life, source counts) so every served score is explainable.

Audience split, because RugBrain's API key is held by public callers while the
detection playbooks are internal:
  * default call  — exploit / hack / scam / advisory items only;
  * ``kind=detection`` or ``include_internal=1`` — requires the operator's
    ``x-internal-code-key`` (``RUGBRAIN_INTERNAL_CODE_KEY``), fail closed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from rugbrain.knowledge_boundary import internal_code_access_allowed

router = APIRouter(prefix="/api/v1/intel", tags=["intel-ranking"])

DEFAULT_RANKING_FILE = "/app/data/ranking/juicy_ranking.json"
PUBLIC_KINDS = frozenset({"exploit", "hack", "scam", "advisory"})
INTERNAL_KINDS = frozenset({"detection"})


def _ranking_path() -> Path:
    return Path(os.getenv("RUGBRAIN_JUICY_RANKING_FILE", DEFAULT_RANKING_FILE))


def load_ranking(path: Path | None = None) -> dict[str, Any]:
    """Read the materialised file; 503 (never an empty 200) when it is missing."""
    target = path or _ranking_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ranking_not_built",
                "hint": "run scripts/rank_juicy_content.py (rugbrain-juicy-ranking.timer)",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={"error": "ranking_unreadable"}) from exc


def _wants_internal(kind: str, include_internal: bool) -> bool:
    return include_internal or kind in INTERNAL_KINDS


def _resolve_kind(kind: str) -> str:
    """Normalise the kind filter and reject an unknown one before any read."""
    normalised = (kind or "").strip().lower()
    if normalised and normalised not in PUBLIC_KINDS | INTERNAL_KINDS:
        raise HTTPException(status_code=400, detail={"error": "unknown_kind"})
    return normalised


def _require_internal_access(wants_internal: bool, key: str | None) -> None:
    """Fail closed before reading when an internal-only view is requested."""
    if not wants_internal:
        return
    if not internal_code_access_allowed({"x-internal-code-key": key or ""}):
        raise HTTPException(status_code=403, detail={"error": "internal_only"})


def _select(items: list[dict[str, Any]], kind: str, wants_internal: bool) -> list[dict[str, Any]]:
    """Filter the materialised items by kind (or by the audience's allowed kinds)."""
    if kind:
        return [item for item in items if item.get("kind") == kind]
    allowed = INTERNAL_KINDS | PUBLIC_KINDS if wants_internal else PUBLIC_KINDS
    return [item for item in items if item.get("kind") in allowed]


def _envelope(
    document: dict[str, Any],
    selected: list[dict[str, Any]],
    kind: str,
    wants_internal: bool,
    limit: int,
) -> dict[str, Any]:
    """Served document header (weights, half-life, counts) plus the page of items."""
    return {
        "generated_at": document.get("generated_at"),
        "generated_at_epoch": document.get("generated_at_epoch"),
        "schema_version": document.get("schema_version"),
        "weights": document.get("weights"),
        "half_life_days": document.get("half_life_days"),
        "kind": kind or None,
        "include_internal": wants_internal,
        "total": len(selected),
        "counts": document.get("counts"),
        "items": selected[:limit],
    }


@router.get("/ranking")
def intel_ranking(
    limit: int = Query(default=20, ge=1, le=200),
    kind: str = Query(
        default="", description="exploit|hack|scam|advisory|detection ('' = all public)"
    ),
    include_internal: bool = Query(default=False),
    x_internal_code_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Top-ranked crypto incidents/advisories, optionally including detection docs."""
    resolved = _resolve_kind(kind)
    wants_internal = _wants_internal(resolved, include_internal)
    _require_internal_access(wants_internal, x_internal_code_key)
    document = load_ranking()
    selected = _select(document.get("items") or [], resolved, wants_internal)
    return _envelope(document, selected, resolved, wants_internal, limit)
