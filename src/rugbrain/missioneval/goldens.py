"""
Mission golden set — the crypto-crime eval contract.

``rugbrain.eval`` (the pre-existing harness) has a 50-row golden set that is
40/50 code-retrieval and **0/50** ``provenance="verified"``; the matching
assertion in ``tests/test_eval_harness.py`` FAILS on the base commit. It
measures retrieval mechanics, not crime competence.

This package measures crime competence with **graded** labels:

1. **Graded, not binary.** Each row carries ``match_terms`` (attack vector,
   scam category, domain) that promote a top-5 hit to gold on text match, and
   ``partial_terms`` for right-family credit. Surfacing "some reentrancy PoC"
   is not the same as surfacing the exact incident, and the score says so.
2. **Corpus-verified.** Every ``relevant_doc_ids`` entry was observed live in
   Qdrant on 2026-09-28 (dim 2560, Qwen3-Embedding-4B) — see each row's
   ``provenance_note``. No row claims a gold doc we have not seen.
3. **Mission-shaped.** Seven job classes matching the mandate: ``rug_history``,
   ``scam_detection``, ``exploit_knowledge``, ``news``, ``market``,
   ``technical_analysis``, ``skill_reference``.

A row whose gold doc is absent from the corpus scores ``miss`` and drags the
number down. That is the honest signal we want — not a green tick.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# The seven mission job classes this set is designed to cover.
JOB_CLASSES = (
    "rug_history",
    "scam_detection",
    "exploit_knowledge",
    "news",
    "market",
    "technical_analysis",
    "skill_reference",
)


class MissionCase(BaseModel):
    """One mission-eval row: a crypto-crime question with graded gold."""

    model_config = ConfigDict(extra="ignore")

    id: str
    job_class: str
    query: str
    collection: str | None = None
    # Exact Qdrant point ids that are unambiguously correct (seen live).
    relevant_doc_ids: list[str] = Field(default_factory=list)
    # Substrings; a top-5 hit whose text contains one is graded gold.
    match_terms: list[str] = Field(default_factory=list)
    # Substrings; a hit containing one is graded partial (right family).
    partial_terms: list[str] = Field(default_factory=list)
    provenance: str = "verified"
    provenance_note: str = ""


def all_cases() -> list[MissionCase]:
    """Every mission case, in stable order (core rows, then exploit rows)."""
    from rugbrain.missioneval.golden_cases import CASES
    from rugbrain.missioneval.golden_cases_exploit import EXPLOIT_CASES

    return [*CASES, *EXPLOIT_CASES]


def cases_for(job_class: str | None = None) -> list[MissionCase]:
    """Return all mission cases, optionally filtered to one job class."""
    return [c for c in all_cases() if job_class is None or c.job_class == job_class]


__all__ = ["JOB_CLASSES", "MissionCase", "all_cases", "cases_for"]
