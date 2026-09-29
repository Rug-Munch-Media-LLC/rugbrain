"""Mission-eval runner — run the graded crime set and aggregate per job class.

Deliberately thin: the grading math is in :mod:`judge`, the data is in
:mod:`goldens`. The runner only decides *what* to run and *how to summarise*.

Metrics reported (all 0..1 unless noted):

* ``gold_rate``  — fraction of cases with a ``gold`` hit inside the top-``k``.
* ``hit_rate``   — ``gold`` **or** ``partial`` (right neighbourhood surfaced).
* ``mrr``        — mean reciprocal rank of the first ``gold`` hit.
* ``pass_rate``  — gold, partial, **or a correctly-absent negative**.
* ``neg_score``  — of the NEGATIVE cases only, the fraction graded
  ``absent``. This is reported ALONGSIDE the headline, never instead of it.
* ``neg_total`` / ``neg_gold`` — the counts behind ``neg_score``.
* ``per_class``  — the same numbers, broken out by the mission job classes, so
  a collapse in (say) ``technical_analysis`` is not hidden by a strong
  ``exploit_knowledge``.

NEGATIVES ARE IN THE HEADLINE, AND HERE IS WHY
-----------------------------------------------
A negative case is one whose correct answer is "this corpus does not cover it".
Two ways to handle it were available and both are wrong:

* **Exclude them from the ratio.** This is the tempting one, and it is a cheat:
  it raises ``gold_rate`` by deleting the rows that test something the other 56
  do not, and it makes a broken retriever look identical to a healthy one,
  because excluded rows contribute to no denominator.
* **Score them as ``miss``, as the harness did before the fourth grade
  existed.** This is the other cheat, and it is the one that was live: a
  correct refusal and a retrieval failure were the same number, so the reported
  0.765 was partly produced by rows that demonstrated nothing. That is the
  defect this module fixes, not a defect to preserve.

So negatives are scored, counted in ``total``, and included in the headline —
a correctly-absent negative is a PASS. The consequence is arithmetic and
intended: ``gold_rate`` FALLS, because five (now twelve) rows stop being
accidentally credited as retrieval successes they never were. ``pass_rate`` is
published next to it as the honest "did the system do the right thing" number,
and ``neg_score`` is published next to that, so the headline cannot hide the
negative result and the negative result cannot hide the headline. Any of the
three can be compared across runs, because all three are always present.

``mrr`` stays gold-only: a correctly-absent case has no rank, so folding it in
would mean inventing a reciprocal rank for "nothing", which is the same
fabrication as grading it as a miss.

Errors are **not** silently dropped: a run where the retriever raised for
every case reports ``status="unavailable"`` and the gate turns that into a
failure. The whole point is to stop a 100%-error run exiting 0.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rugbrain.missioneval.goldens import MissionCase, all_cases
from rugbrain.missioneval.judge import (
    ABSENT,
    GOLD,
    MISS,
    Grade,
    grade_absence,
    grade_hits,
)

# Default: gold must appear within the first 5 ranks to count as a hit.
DEFAULT_TOP_K = 10
DEFAULT_GOLD_RANK_CAP = 5


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


@dataclass
class CaseScore:
    """Graded outcome for one mission case."""

    case_id: str
    job_class: str
    query: str
    collection: str | None
    grade: str
    best_rank: int
    error: str = ""
    evidence: str = ""
    latency_ms: float = 0.0
    negative: bool = False
    retrieved_count: int = 0

    @property
    def is_gold(self) -> bool:
        return self.grade == GOLD

    @property
    def is_retrieval_hit(self) -> bool:
        """Gold or partial ONLY — the retriever surfaced the right neighbourhood.

        ``absent`` is deliberately excluded. ``hit_rate`` has a 0.78 CI floor
        that predates the fourth grade; folding a new grade into it via
        ``grade != MISS`` would raise a gated number with zero retrieval
        improvement, which is the ratchet-weakening this module exists to
        prevent. Absence is reported through ``pass_rate`` and ``neg_score``.
        """
        return self.grade in (GOLD, "partial")

    @property
    def is_pass(self) -> bool:
        return self.grade in (GOLD, "partial", ABSENT)


@dataclass
class MissionReport:
    """Aggregate mission-eval report."""

    total: int = 0
    errors: int = 0
    status: str = "ok"  # ok | unavailable | empty
    top_k: int = DEFAULT_TOP_K
    gold_rate: float = 0.0
    hit_rate: float = 0.0
    pass_rate: float = 0.0
    mrr: float = 0.0
    neg_score: float = 0.0
    neg_total: int = 0
    neg_gold: int = 0
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    per_case: list[CaseScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "errors": self.errors,
            "top_k": self.top_k,
            "gold_rate": round(self.gold_rate, 4),
            "hit_rate": round(self.hit_rate, 4),
            "pass_rate": round(self.pass_rate, 4),
            "mrr": round(self.mrr, 4),
            "neg_score": round(self.neg_score, 4),
            "neg_total": self.neg_total,
            "neg_gold": self.neg_gold,
            "per_class": {
                k: {m: round(v, 4) for m, v in d.items()} for k, d in self.per_class.items()
            },
            "per_case": [
                {
                    "id": c.case_id,
                    "job_class": c.job_class,
                    "grade": c.grade,
                    "best_rank": c.best_rank,
                    "negative": c.negative,
                    "retrieved_count": c.retrieved_count,
                    "error": c.error,
                    "evidence": c.evidence,
                }
                for c in self.per_case
            ],
        }


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


async def score_case(
    retriever: Callable[..., Any],
    case: MissionCase,
    top_k: int = DEFAULT_TOP_K,
    gold_rank_cap: int = DEFAULT_GOLD_RANK_CAP,
) -> CaseScore:
    """Run one mission case through *retriever* and grade the result."""
    score = CaseScore(
        case_id=case.id,
        job_class=case.job_class,
        query=case.query,
        collection=case.collection,
        grade=MISS,
        best_rank=0,
        negative=is_negative(case),
    )
    start = time.perf_counter()
    try:
        docs = await retriever(case.query, case.collection, top_k)
    except Exception as exc:
        score.error = f"{type(exc).__name__}: {exc}"
        score.latency_ms = (time.perf_counter() - start) * 1000.0
        return score
    score.latency_ms = (time.perf_counter() - start) * 1000.0

    retrieved = list(docs or [])
    score.retrieved_count = len(retrieved)
    grades = grade_hits(
        retrieved,
        case.relevant_doc_ids,
        case.match_terms,
        gold_rank_cap=gold_rank_cap,
        partial_terms=case.partial_terms,
    )
    if score.negative:
        # A negative has no gold to find; it is graded on the ABSENCE of the
        # absent vocabulary, gated on the retriever having actually searched.
        # Deliberately NOT reusing ``grades``: those are rank-capped for the
        # positive path, which is the wrong rule here (see judge.grade_absence).
        absent_grade = grade_absence(
            retrieved,
            case.relevant_doc_ids,
            case.match_terms,
            partial_terms=case.partial_terms,
        )
        score.grade, score.best_rank, score.evidence = (
            absent_grade.grade,
            absent_grade.rank,
            absent_grade.why,
        )
        return score
    score.grade, score.best_rank, score.evidence = _classify(grades)
    return score


async def run_mission_eval(
    retriever: Callable[..., Any],
    cases: list[MissionCase] | None = None,
    top_k: int = DEFAULT_TOP_K,
    gold_rank_cap: int = DEFAULT_GOLD_RANK_CAP,
) -> MissionReport:
    """Run the mission golden set and aggregate graded scores per job class."""
    rows = list(cases if cases is not None else all_cases())
    report = MissionReport(total=len(rows), top_k=top_k)
    if not rows:
        report.status = "empty"
        return report

    report.per_case = [await score_case(retriever, c, top_k, gold_rank_cap) for c in rows]
    valid = [s for s in report.per_case if not s.error]
    report.errors = len(report.per_case) - len(valid)
    if not valid:
        report.status = "unavailable"
        return report

    n = len(valid)
    report.gold_rate = sum(s.is_gold for s in valid) / n
    report.hit_rate = sum(s.is_retrieval_hit for s in valid) / n
    report.pass_rate = sum(s.is_pass for s in valid) / n
    report.mrr = sum(_mrr([]) if not s.is_gold else 1.0 / max(s.best_rank, 1) for s in valid) / n

    negatives = [s for s in valid if s.negative]
    report.neg_total = len(negatives)
    report.neg_gold = sum(s.grade == ABSENT for s in negatives)
    report.neg_score = report.neg_gold / len(negatives) if negatives else 0.0

    for cls in {s.job_class for s in valid}:
        sub = [s for s in valid if s.job_class == cls]
        report.per_class[cls] = {
            "n": len(sub),
            "gold_rate": sum(s.is_gold for s in sub) / len(sub),
            "hit_rate": sum(s.is_retrieval_hit for s in sub) / len(sub),
            "pass_rate": sum(s.is_pass for s in sub) / len(sub),
        }
    return report
