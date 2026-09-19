"""
Baseline-set selection — pick the baseline file that matches the query set.

The eval harness runs two query sets:

* **smoke** — a small (default 10) prefix of the golden set, used by
  ``scripts/run-eval-smoke.sh`` in CI. Baselines live in
  ``eval/baselines.smoke.json``.
* **full** — every golden row, used by ``scripts/run-eval-nightly.sh``.
  Baselines live in ``eval/baselines.full.json``.

Gating a 10-query subset against a full-set baseline (or vice versa) is
meaningless: the two sets have different metric distributions. This module
maps a *selected* query set to the correct baseline path so callers never
have to remember which file to pass.

Selection rule (deterministic, no magic):

* ``limit is None`` or ``limit >= total`` → **full**.
* otherwise → **smoke**.

``total`` is the number of golden rows available; when unknown, a caller may
pass ``total=None`` and the rule falls back to ``limit is None``.
"""

from __future__ import annotations

from pathlib import Path

# Baseline files shipped with the package.
EVAL_DIR = Path(__file__).resolve().parent
SMOKE_BASELINES = EVAL_DIR / "baselines.smoke.json"
FULL_BASELINES = EVAL_DIR / "baselines.full.json"

# Backwards-compatible alias for the pre-split single baseline file.
LEGACY_BASELINES = EVAL_DIR / "baselines.json"

SMOKE = "smoke"
FULL = "full"


def select_baseline_path(limit: int | None, total: int | None = None) -> Path:
    """
    Return the baseline path matching the selected query set.

    Args:
        limit: the ``limit`` passed to :func:`~rugbrain.eval.harness.run_backtest`
            (``None`` means "all rows").
        total: number of golden rows available, when known. Used to detect a
            ``limit`` that covers the whole set (which is a full run).

    Returns:
        ``FULL_BASELINES`` for a full run, else ``SMOKE_BASELINES``.
    """
    if limit is None:
        return FULL_BASELINES
    if total is not None and limit >= total:
        return FULL_BASELINES
    return SMOKE_BASELINES


def select_baseline_name(limit: int | None, total: int | None = None) -> str:
    """Return ``"full"`` or ``"smoke"`` for the selected query set."""
    return FULL if select_baseline_path(limit, total) is FULL_BASELINES else SMOKE


__all__ = [
    "EVAL_DIR",
    "FULL",
    "FULL_BASELINES",
    "LEGACY_BASELINES",
    "SMOKE",
    "SMOKE_BASELINES",
    "select_baseline_name",
    "select_baseline_path",
]
