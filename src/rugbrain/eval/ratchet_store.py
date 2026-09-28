"""Baseline persistence for the retrieval ratchet — read and write, nothing else.

Split out of ``baseline_ratchet`` so the storage question ("what is on disk,
and is it allowed to change?") is separate from the policy question ("is this
run a pass?").

Two invariants:

* An absent baseline is COULD_NOT_MEASURE, never PASS. There is no such thing
  as "no baseline therefore fine".
* Writing a baseline is a separate, explicit action. Re-recording one is how a
  red gate gets made green, so it must never be a side effect of running one.
"""

from __future__ import annotations

import json
from pathlib import Path

from rugbrain.eval.ratchet_model import BASELINE_SCHEMA, Measurement, dumps


def load_baseline(path: Path | str) -> Measurement | None:
    """Load the stored baseline. ``None`` when absent or unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != BASELINE_SCHEMA:
        raise ValueError(
            f"baseline schema mismatch in {path}: expected {BASELINE_SCHEMA}, "
            f"got {raw.get('schema')!r}"
        )
    return Measurement.from_dict(raw.get("measurement") or {})


def write_baseline(path: Path | str, measurement: Measurement) -> None:
    """Record a measurement as the new baseline. Explicit call only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BASELINE_SCHEMA,
        "measurement": measurement.as_dict(),
    }
    path.write_text(dumps(payload), encoding="utf-8")


__all__ = ["load_baseline", "write_baseline"]
