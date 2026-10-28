"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The result envelope: what one run reports, and the only way a run ends.

Every exit — success, refusal, failure — goes through :func:`finish`, so a
status is always paired with a journal row and a health line. A run that
returns without journaling is a run an operator cannot see.
"""

from __future__ import annotations

import time
from typing import Any

from rugbrain.content_daily import editions
from rugbrain.content_daily.journal import Journal
from rugbrain.content_daily.stage_runner import StageOutcome


def envelope(
    edition: editions.Edition, day: str, run_key: str, dry_run: bool
) -> dict[str, Any]:
    """The empty envelope for one run."""
    return {
        "run_key": run_key,
        "edition": edition.key,
        "label": edition.label,
        "day": day,
        "dry_run": dry_run,
        "status": "running",
        "title": edition.title_for(day),
        "url": None,
        "message_ids": [],
        "items": 0,
        "dedupe_drops": 0,
        "suppressed_refs": 0,
        "stages": [],
        "errors": [],
        "health": "",
    }


def note(result: dict[str, Any], outcome: StageOutcome, name: str) -> None:
    """Append one stage line to the envelope and any error it produced."""
    result["stages"].append(
        {
            "name": name,
            "ok": outcome.ok,
            "ms": outcome.ms,
            "attempts": outcome.attempts,
            "error_class": outcome.error_class,
        }
    )
    if not outcome.ok and outcome.error:
        result["errors"].append(f"{name}: {outcome.error_class}: {outcome.error}")


def finish(
    result: dict[str, Any],
    journal: Journal,
    started: float,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    """Stamp, journal and return the envelope — the only exit path."""
    result.update(extra)
    result["status"] = status
    result["ms"] = round((time.perf_counter() - started) * 1000, 1)
    journal.run(result)
    result["health"] = journal.health_line()
    return result


__all__ = ["envelope", "finish", "note"]
