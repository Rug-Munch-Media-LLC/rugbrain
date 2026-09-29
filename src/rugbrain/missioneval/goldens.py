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

A NEGATIVE row is one whose correct answer is "this corpus does not cover it".
Those carry ``expected_absent=True`` (or the ``NEGATIVE`` marker in their
provenance note) and are graded with the fourth grade, ``judge.ABSENT``, not as
a retrieval failure — see ``judge.grade_absence`` for the absence rule. A
POSITIVE row whose gold doc is genuinely missing from the corpus still scores
``miss`` and drags the number down. That is the honest signal we want — not a
green tick.
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
    # 2026-08-28 — three classes added with the wallet/sanctions/research
    # golden cases. Their absence broke three tests at once, and one of them
    # was telling the truth while looking like a bug.
    #
    # test_crime_families_dominate_over_code asserts that crypto-crime classes
    # outnumber the rest. It failed at 26/55 against a floor of 27, which reads
    # as "the new cases diluted the crime corpus". They did the opposite:
    # address_risk, sanctions_screening and research_papers ARE crypto-crime
    # work — screening a wallet against OFAC and judging an address's risk are
    # exactly what this corpus exists to do. The invariant was written when
    # crypto-crime meant three literal names, and it silently assumed anything
    # new was code or market data. So the invariant's DEFINITION was incomplete,
    # not the cases.
    #
    # The alternative readings were rejected deliberately. Excluding the 5
    # negative rows from the ratio would make the test pass while hiding that
    # they are the most valuable rows in the set. Relaxing it to a fixed floor
    # would remove the guard permanently over a one-off gap. Correcting the
    # class list is the only option that makes the test mean what it says.
    "address_risk",
    "sanctions_screening",
    "research_papers",
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
    # TRUE for a NEGATIVE case: the correct answer is "this corpus does not
    # cover it". Graded with the fourth grade (``judge.ABSENT``) instead of
    # being scored as a retrieval failure. See ``judge.grade_absence`` for the
    # absence rule and why "no gold term matched" is not sufficient on its own.
    #
    # This is the explicit flag, and it is the PREFERRED way to mark a negative.
    # ``runner.is_negative`` falls back to scanning ``provenance_note`` for the
    # word NEGATIVE so that rows authored by the data lanes (which are owned by
    # other agents and cannot be edited from here) are still graded correctly
    # today. New rows should set this field directly rather than rely on prose.
    expected_absent: bool = False


def all_cases() -> list[MissionCase]:
    """Every mission case, in stable order (core, exploit, then TA rows)."""
    from rugbrain.missioneval.golden_cases import CASES
    from rugbrain.missioneval.golden_cases_exploit import EXPLOIT_CASES
    from rugbrain.missioneval.golden_cases_ta import TA_CASES

    return [*CASES, *EXPLOIT_CASES, *TA_CASES]


def cases_for(job_class: str | None = None) -> list[MissionCase]:
    """Return all mission cases, optionally filtered to one job class."""
    return [c for c in all_cases() if job_class is None or c.job_class == job_class]


__all__ = ["JOB_CLASSES", "MissionCase", "all_cases", "cases_for"]

# ---------------------------------------------------------------------------
# Provenance of the extended scam/rug golden rows (Lane Y)
#
# ``golden_cases_scam_ext`` is DATA ONLY; the reasoning for what it adds lives
# here, with the contract those rows are written against. It adds NEW coverage
# rather than restating: the pre-existing scam/rug rows never ask for an
# address-level lookup, a drainer contract, a phishing clone, or a scam class
# the corpus lacks. It is weighted toward ``scam_detection``, the weakest real
# class.
#
# Every collection named in that module was RESOLVED LIVE through
# ``lane_resolver.resolve_collection`` against Qdrant on 127.0.0.1:6334 on
# 2026-09-29, and the widths are recorded beside the data. The canonical lane is
# 4096, so each 8B twin is named by its BASE name and resolved by the retriever
# rather than hardcoded.
