"""Grade-reduction helpers for the mission eval (split out of runner.py)."""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase
from rugbrain.missioneval.judge import GOLD, MISS, Grade


def is_negative(case: MissionCase) -> bool:
    """Is this case a NEGATIVE — is "not covered" the correct answer?

    Two sources, in order:

    1. ``MissionCase.expected_absent`` — the explicit flag on the row. This is
       the contract.
    2. The word ``NEGATIVE`` in ``provenance_note`` — a fallback, because the
       golden DATA modules that author these rows are owned by another lane and
       are being edited concurrently. Without it, twelve already-authored
       negatives would all be scored as retrieval failures until their owning
       lane could be given the flag, and the headline would stay inflated for
       exactly as long as that edit is deferred. The fallback reads the author's
       own documented declaration, it does not infer anything from the content.
    """
    if case.expected_absent:
        return True
    return "NEGATIVE" in case.provenance_note.upper()


def _mrr(grades: list[Grade]) -> float:
    for g in grades:
        if g.is_gold:
            return 1.0 / g.rank
    return 0.0


def _classify(grades: list[Grade]) -> tuple[str, int, str]:
    """Reduce per-doc grades to one case-level grade + best rank + evidence."""
    golds = [g for g in grades if g.grade == GOLD]
    if golds:
        best = min(golds, key=lambda g: g.rank)
        return GOLD, best.rank, best.why
    parts = [g for g in grades if g.is_hit]
    if parts:
        best = min(parts, key=lambda g: g.rank)
        return best.grade, best.rank, best.why
    return MISS, 0, ""
