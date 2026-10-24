"""Result types for the mission eval (split out of runner.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rugbrain.missioneval.judge import ABSENT, GOLD

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
