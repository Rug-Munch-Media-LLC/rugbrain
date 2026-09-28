"""Mission-eval runner — run the graded crime set and aggregate per job class.

Deliberately thin: the grading math is in :mod:`judge`, the data is in
:mod:`goldens`. The runner only decides *what* to run and *how to summarise*.

Metrics reported (all 0..1 unless noted):

* ``gold_rate``  — fraction of cases with a ``gold`` hit inside the top-``k``.
* ``hit_rate``   — ``gold`` **or** ``partial`` (right neighbourhood surfaced).
* ``mrr``        — mean reciprocal rank of the first ``gold`` hit.
* ``per_class``  — the same numbers, broken out by the seven mission job
  classes, so a collapse in (say) ``technical_analysis`` is not hidden by a
  strong ``exploit_knowledge``.

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
from rugbrain.missioneval.judge import GOLD, MISS, Grade, grade_hits

# Default: gold must appear within the first 5 ranks to count as a hit.
DEFAULT_TOP_K = 10
DEFAULT_GOLD_RANK_CAP = 5


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

    @property
    def is_gold(self) -> bool:
        return self.grade == GOLD


@dataclass
class MissionReport:
    """Aggregate mission-eval report."""

    total: int = 0
    errors: int = 0
    status: str = "ok"  # ok | unavailable | empty
    top_k: int = DEFAULT_TOP_K
    gold_rate: float = 0.0
    hit_rate: float = 0.0
    mrr: float = 0.0
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
            "mrr": round(self.mrr, 4),
            "per_class": {
                k: {m: round(v, 4) for m, v in d.items()} for k, d in self.per_class.items()
            },
            "per_case": [
                {
                    "id": c.case_id,
                    "job_class": c.job_class,
                    "grade": c.grade,
                    "best_rank": c.best_rank,
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
    )
    start = time.perf_counter()
    try:
        docs = await retriever(case.query, case.collection, top_k)
    except Exception as exc:
        score.error = f"{type(exc).__name__}: {exc}"
        score.latency_ms = (time.perf_counter() - start) * 1000.0
        return score
    score.latency_ms = (time.perf_counter() - start) * 1000.0

    grades = grade_hits(
        list(docs or []),
        case.relevant_doc_ids,
        case.match_terms,
        gold_rank_cap=gold_rank_cap,
        partial_terms=case.partial_terms,
    )
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
    report.hit_rate = sum(s.grade != MISS for s in valid) / n
    report.mrr = sum(_mrr([]) if not s.is_gold else 1.0 / max(s.best_rank, 1) for s in valid) / n

    for cls in {s.job_class for s in valid}:
        sub = [s for s in valid if s.job_class == cls]
        report.per_class[cls] = {
            "n": len(sub),
            "gold_rate": sum(s.is_gold for s in sub) / len(sub),
            "hit_rate": sum(s.grade != MISS for s in sub) / len(sub),
        }
    return report
