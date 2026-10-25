"""Build the live corpus map and render its <=40-line brief form."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugbrain.corpus_awareness.probe import TS_KEYS, QdrantUnreachable
from rugbrain.corpus_awareness.questions import question_for
from rugbrain.corpus_awareness.routes import exposed_by

_MAX_BRIEF_LINES = 40
_TITLE_KEYS = ("title", "headline", "name", "source_title", "function_name", "story", "url")


def _title(payload: dict[str, Any]) -> str:
    for key in _TITLE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:70]
    return "(no title field)"


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value > 1_000_000_000 else None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    return None


def _freshness(samples: list[dict[str, Any]]) -> dict[str, Any]:
    stamps = [_parse_ts(p.get(k)) for p in samples for k in TS_KEYS if p.get(k)]
    newest = max((s for s in stamps if s), default=None)
    if newest is None:
        return {"age_hours": None, "label": "unknown", "latest": None}
    age = (datetime.now(UTC).timestamp() - newest) / 3600
    label = "fresh" if age <= 24 else ("aging" if age <= 24 * 7 else "stale")
    return {
        "age_hours": round(age, 1),
        "label": label,
        "latest": datetime.fromtimestamp(newest, UTC).isoformat(timespec="seconds"),
    }


def render_brief(rows: list[dict[str, Any]]) -> str:
    """One line per collection, hard-capped at 40 lines including the header."""
    head = f"CORPUS (live qdrant) — {len(rows)} collections · points · dim · freshness · exposed · Q · samples"
    lines = [head]
    for row in rows[:_MAX_BRIEF_LINES - 2]:
        fresh = row["freshness"]
        age = "?" if fresh["age_hours"] is None else f"{fresh['age_hours']}h"
        samples = " | ".join(row["sample_titles"][:3])[:90] or "none"
        lines.append(
            f"{row['collection']} :: {row['points']}pts · {row['dim'] or '?'}d · "
            f"{fresh['label']}/{age} · {row['exposed_by']} · Q: {row['answers']} · "
            f"empty={row['empty']} · top: {samples}"
        )
    if len(rows) > _MAX_BRIEF_LINES - 2:
        lines.append(f"... +{len(rows) - (_MAX_BRIEF_LINES - 2)} more collections (use detail='full')")
    return "\n".join(lines[:_MAX_BRIEF_LINES])


async def build_corpus_map(probe: Any, detail: str = "brief") -> dict[str, Any]:
    """Live map of every collection: points, dim, freshness, question, exposure, samples."""
    level = "full" if detail == "full" else "brief"
    try:
        names = await probe.collections()
    except QdrantUnreachable as exc:
        return {
            "status": "refused",
            "detail": level,
            "error": {"code": "qdrant_unreachable", "reason": str(exc)[:300]},
            "collections": [],
        }
    want = 5 if level == "full" else 3
    rows: list[dict[str, Any]] = []
    for name in names:
        try:
            described = await probe.describe(name, want)
        except QdrantUnreachable as exc:
            return {
                "status": "refused",
                "detail": level,
                "error": {"code": "qdrant_unreachable", "reason": str(exc)[:300]},
                "collections": rows,
            }
        payloads = described["samples"]
        row: dict[str, Any] = {
            "collection": name,
            "points": described["points"],
            "dim": described["dim"],
            "freshness": _freshness(payloads),
            "empty": described["points"] == 0,
            "answers": question_for(name),
            "exposed_by": exposed_by(name),
            "sample_titles": [_title(p) for p in payloads],
        }
        if level == "full":
            row["payload_keys"] = sorted({k for p in payloads for k in p})
            row["samples"] = payloads
            if described["points"] == 0:
                row["samples"] = []
        rows.append(row)
    result: dict[str, Any] = {
        "status": "ok",
        "detail": level,
        "collection_count": len(rows),
        "collections": rows,
    }
    if level == "brief":
        result["text"] = render_brief(rows)
    return result
