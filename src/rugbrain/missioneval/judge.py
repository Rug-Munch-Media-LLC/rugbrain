"""Graded relevance judge for mission-eval retrieval rows.

Three grades, in descending quality:

* ``gold``    — the row is a top-5 retrieved doc whose *text or entity* is the
  thing the query asks about.
* ``partial`` — related but not the specific ask (right topic, wrong instance,
  or a different technique in the same family).
* ``miss``    — nothing retrieved in the top-k touches the query.

Grading is deliberately deterministic and offline: an exact Qdrant point-id
match is ``gold``; otherwise the row's ``match_terms`` (lowercased substrings,
e.g. an attack vector, a scam category, a domain) are searched in the retrieved
content. That keeps the eval honest without an LLM judge in the loop, and it
degrades to ``miss`` rather than to credit when the corpus genuinely lacks the
answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Grade constants (kept as plain strings so they serialise into JSON reports).
GOLD = "gold"
PARTIAL = "partial"
MISS = "miss"


@dataclass(frozen=True)
class Grade:
    """One graded retrieval result for one mission query."""

    grade: str
    rank: int
    doc_id: str
    why: str
    matched_term: str = ""

    @property
    def is_gold(self) -> bool:
        return self.grade == GOLD

    @property
    def is_hit(self) -> bool:
        """Gold or partial — i.e. the retriever surfaced the right neighbourhood."""
        return self.grade in (GOLD, PARTIAL)


def grade_hits(
    retrieved: list[dict[str, Any]],
    relevant_doc_ids: list[str],
    match_terms: list[str],
    gold_rank_cap: int = 5,
    partial_terms: list[str] | None = None,
) -> list[Grade]:
    """Grade each retrieved doc against a mission query's ground truth.

    Args:
        retrieved: docs in rank order, each with an ``id`` and ``content``.
        relevant_doc_ids: exact point ids that count as ``gold`` at any rank.
        match_terms: substrings that promote a doc to ``gold`` (an attack
            vector, a scam category, a domain). Compared case-insensitively.
        gold_rank_cap: only the first N ranks can earn ``gold`` by term match —
            a doc that finally surfaces at rank 40 did not really answer.
        partial_terms: substrings that earn ``partial`` when no gold matched.

    Returns:
        One :class:`Grade` per retrieved doc, in rank order.
    """
    gold_ids = {str(d) for d in relevant_doc_ids}
    gold_terms = [t.lower() for t in match_terms if t]
    part_terms = [t.lower() for t in (partial_terms or []) if t]
    out: list[Grade] = []
    gold_seen = False

    for rank, doc in enumerate(retrieved, start=1):
        doc_id = str(doc.get("id", ""))
        content = (doc.get("content") or "").lower()
        text = " ".join(
            str(doc.get(k) or "") for k in ("content", "title", "description", "code_block")
        ).lower()

        if doc_id in gold_ids:
            gold_seen = True
            out.append(Grade(GOLD, rank, doc_id, "exact qdrant point id", "id"))
            continue
        if gold_seen or rank > gold_rank_cap:
            out.append(Grade(MISS, rank, doc_id, "after the gold cap" if not gold_seen else "gold already found"))
            continue
        term = next((t for t in gold_terms if t and (t in content or t in text)), "")
        if term:
            gold_seen = True
            out.append(Grade(GOLD, rank, doc_id, "match term in retrieved text", term))
            continue
        part = next((t for t in part_terms if t and (t in content or t in text)), "")
        if part:
            out.append(Grade(PARTIAL, rank, doc_id, "related family term", part))
            continue
        out.append(Grade(MISS, rank, doc_id, "no match term"))
    return out


__all__ = ["GOLD", "MISS", "PARTIAL", "Grade", "grade_hits"]
