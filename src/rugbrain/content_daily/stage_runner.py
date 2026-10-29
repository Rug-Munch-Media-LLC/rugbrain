"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

One stage, one journal row: timing, item counts, attempt trail, error class.

Kept apart from the pipeline so the orchestration reads as the sequence of
stages and nothing else. A failed stage returns an outcome; it does not raise —
whether a failure aborts the run is the pipeline's decision, recorded in the
contract as ``fatal``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rugbrain.content_daily.journal import Journal
from rugbrain.content_daily.retry import StageError, attempt
from rugbrain.content_daily.spec import StageSpec


@dataclass(frozen=True)
class StageOutcome:
    """What a stage produced, or why it did not."""

    ok: bool
    value: Any = None
    ms: float = 0.0
    attempts: int = 1
    error_class: str | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _count(value: Any) -> int | None:
    """Item count for the journal; None when the stage produces a scalar."""
    if isinstance(value, dict):
        return None
    try:
        return len(value)
    except TypeError:
        return None


async def run_stage(
    stage: StageSpec,
    fn: Callable[[], Awaitable[Any]],
    *,
    journal: Journal,
    run_key: str,
    edition: str,
    day: str,
    items_in: int | None = None,
) -> StageOutcome:
    """Execute ``fn`` under ``stage``'s retry policy and journal the outcome."""
    started = time.perf_counter()
    try:
        value, trail = await attempt(stage, fn)
    except StageError as exc:
        journal.stage(
            run_key,
            edition=edition,
            day=day,
            stage=stage.name,
            ok=False,
            ms=(time.perf_counter() - started) * 1000,
            items_in=items_in,
            attempts=len(exc.attempts),
            error_class=exc.error_class,
            detail={"trail": exc.attempts[-3:], "failure_mode": stage.failure_mode},
        )
        return StageOutcome(
            ok=False,
            ms=round((time.perf_counter() - started) * 1000, 1),
            attempts=len(exc.attempts),
            error_class=exc.error_class,
            error=exc.last,
        )

    journal.stage(
        run_key,
        edition=edition,
        day=day,
        stage=stage.name,
        ok=True,
        ms=(time.perf_counter() - started) * 1000,
        items_in=items_in,
        items_out=_count(value) if not isinstance(value, dict) else None,
        attempts=len(trail),
    )
    return StageOutcome(
        ok=True,
        value=value,
        ms=round((time.perf_counter() - started) * 1000, 1),
        attempts=len(trail),
    )


__all__ = ["StageOutcome", "run_stage"]
