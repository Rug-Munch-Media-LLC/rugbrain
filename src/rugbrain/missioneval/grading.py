"""Extracted from the original module — moved verbatim."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GOLD = "gold"
PARTIAL = "partial"
#: The fourth grade: a negative case where the corpus genuinely lacks the answer
#: AND the retriever demonstrably searched. Equal in standing to GOLD.
ABSENT = "absent"
MISS = "miss"

#: A negative case is only ``absent`` if the retriever returned at least this
#: many documents. Zero gold-term matches is also what a DEAD retriever
#: produces, so the match result alone cannot carry the claim; the result page
#: has to be non-empty to show the search actually happened. See the module
#: docstring. Set to 0 to get the worthless rule back — do not.
MIN_RETRIEVED_FOR_ABSENCE = 5


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

    @property
    def is_pass(self) -> bool:
        """Gold, partial, or a correctly-absent negative.

        This is the only predicate that means "the system behaved correctly".
        Gold-only is ``is_gold``; absence must be credited or the harness
        cannot tell a correct refusal from a retrieval failure.
        """
        return self.grade in (GOLD, PARTIAL, ABSENT)


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
            out.append(
                Grade(
                    MISS,
                    rank,
                    doc_id,
                    "after the gold cap" if not gold_seen else "gold already found",
                )
            )
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
