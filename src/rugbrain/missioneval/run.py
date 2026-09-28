"""Blocking runner entry point for the mission eval (scripts/CLI/tests).

Kept separate from :mod:`runner` so the async core stays under the 200-line
cap. ``run_mission_eval_sync`` is the only thing scripts need.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from rugbrain.missioneval.goldens import MissionCase
from rugbrain.missioneval.runner import (
    DEFAULT_GOLD_RANK_CAP,
    DEFAULT_TOP_K,
    MissionReport,
)


def run_mission_eval_sync(
    retriever: Callable[..., Any],
    cases: list[MissionCase] | None = None,
    top_k: int = DEFAULT_TOP_K,
    gold_rank_cap: int = DEFAULT_GOLD_RANK_CAP,
) -> MissionReport:
    """Run the mission golden set synchronously and return the report."""
    return asyncio.run(run_mission_eval(retriever, cases, top_k, gold_rank_cap))


async def run_mission_eval(
    retriever: Callable[..., Any],
    cases: list[MissionCase] | None = None,
    top_k: int = DEFAULT_TOP_K,
    gold_rank_cap: int = DEFAULT_GOLD_RANK_CAP,
) -> MissionReport:
    """Async core — see :func:`rugbrain.missioneval.runner.run_mission_eval`."""
    from rugbrain.missioneval.runner import run_mission_eval as _run

    return await _run(retriever, cases, top_k, gold_rank_cap)


__all__ = ["run_mission_eval", "run_mission_eval_sync"]
