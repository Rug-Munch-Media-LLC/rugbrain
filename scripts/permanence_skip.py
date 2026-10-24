"""Skip re-uploading a Qdrant collection whose content has not changed.

MEASURED DEFECT (2026-10-24, tier-2 cost reclaim): the nightly permanence
writer re-uploaded ``codebase_reference_v2.parquet`` (~35 GiB) every single day
although the collection had not changed -- ~1 TiB/month for one unchanged
file. Each day owns its own ``snapshots/<date>/`` prefix, so the existing
``todays_done_collections`` resume can never see yesterday's upload: it only
answers "done today".

This module is the missing cross-day gate. A collection is skipped when its
fingerprint (point count + vector config + payload schema) matches the last
successful export; the manifest entry is then carried forward pointing at the
ORIGINAL object, so a restore of today's date still resolves to real bytes.

Bounded staleness: never skipped more than ``B2_SKIP_UNCHANGED_MAX_RUNS``
consecutive times (default 6), so a payload-only edit that leaves the
fingerprint unchanged is still re-exported weekly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("permanence_skip")

DEFAULT_STATE = "/var/lib/rugbrain/permanence/export-state.json"


def _state_path() -> Path:
    return Path(os.environ.get("B2_PERMANENCE_STATE", DEFAULT_STATE))


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(path)


def _fingerprint(client: Any, name: str) -> dict[str, Any]:
    """Stable content signature: what changes when the collection changes.

    ``segments_count`` is deliberately excluded -- Qdrant re-segments during
    optimisation, which would make every collection look changed every night.
    """
    info = client.get_collection(name)
    vectors = getattr(info.config.params, "vectors", None)
    sparse = getattr(info.config.params, "sparse_vectors", None)
    return {
        "points": int(getattr(info, "points_count", 0) or 0),
        "vectors": json.dumps(vectors, default=str, sort_keys=True),
        "sparse": json.dumps(sparse, default=str, sort_keys=True),
        "payload_schema": json.dumps(
            sorted(getattr(info, "payload_schema", {}) or {}), default=str
        ),
    }


def carry_if_unchanged(client: Any, name: str) -> dict[str, Any] | None:
    """Return today's manifest entry if the collection may be skipped.

    ``None`` means the caller must export and upload as usual.
    """
    if os.environ.get("B2_SKIP_UNCHANGED", "1") not in ("1", "true", "yes"):
        return None
    max_runs = int(os.environ.get("B2_SKIP_UNCHANGED_MAX_RUNS", "6"))
    path = _state_path()
    prior = _load(path).get(name) or {}
    entry = prior.get("entry") or {}
    if not entry.get("object_key") or entry.get("partial"):
        return None
    runs = int(prior.get("skip_runs", 0))
    if runs >= max_runs:
        logger.info("skip_recheck collection=%s runs=%d (max reached)", name, runs)
        return None
    try:
        same = _fingerprint(client, name) == prior.get("fingerprint")
    except Exception as exc:  # noqa: BLE001 - never block a backup on the gate
        logger.warning("skip_fingerprint_failed collection=%s err=%s", name, exc)
        return None
    if not same:
        return None

    state = _load(path)
    state[name] = {**prior, "skip_runs": runs + 1, "last_skip": datetime.now(UTC).isoformat()}
    _save(path, state)
    now = datetime.now(UTC).isoformat()
    carried = dict(entry)
    carried.update(
        {
            "carried_from": entry.get("finished_at") or entry.get("started_at"),
            "carried_reason": "unchanged",
            "skipped_upload": True,
            "bytes_stored": 0,
            "started_at": now,
            "finished_at": now,
        }
    )
    logger.info(
        "skip_unchanged collection=%s points=%s key=%s run=%d/%d",
        name,
        entry.get("point_count"),
        entry.get("object_key"),
        runs + 1,
        max_runs,
    )
    return carried


def record_export(name: str, entry: dict[str, Any], client: Any) -> None:
    """Remember a successful export so tomorrow can skip it if unchanged."""
    path = _state_path()
    state = _load(path)
    state[name] = {
        "fingerprint": _fingerprint(client, name),
        "entry": {k: v for k, v in entry.items() if k not in ("carried_from", "skipped_upload")},
        "skip_runs": 0,
        "recorded_at": datetime.now(UTC).isoformat(),
        "recorded_ts": time.time(),
    }
    _save(path, state)
