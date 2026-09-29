"""Graded relevance judge for mission-eval retrieval rows.

Four grades, in descending quality:

* ``gold``      — the row is a top-5 retrieved doc whose *text or entity* is the
  thing the query asks about.
* ``partial``   — related but not the specific ask (right topic, wrong instance,
  or a different technique in the same family).
* ``absent``    — the row is a NEGATIVE case (no gold exists) and the retrieval
  behaved as a healthy retriever should: it searched, returned a plausible
  neighbour, and none of it touched the absent vocabulary. See the absence rule
  below. **This is a pass.**
* ``miss``      — nothing retrieved in the top-k touches the query.

Grading is deliberately deterministic and offline: an exact Qdrant point-id
match is ``gold``; otherwise the row's ``match_terms`` (lowercased substrings,
e.g. an attack vector, a scam category, a domain) are searched in the retrieved
content. That keeps the eval honest without an LLM judge in the loop, and it
degrades to ``miss`` rather than to credit when the corpus genuinely lacks the
answer.

THE ABSENCE RULE — why a negative needs a FLOOR and not just zero matches
---------------------------------------------------------------------
A negative golden case asks a question the corpus does not cover. The obvious
way to score it is "no gold term matched, therefore correctly absent". That rule
is worthless: a retriever that is completely broken — one that raises on every
call, or that hits a dead embed lane and returns nothing, or that searches the
wrong collection — also produces zero gold-term matches. Gold-absence alone is
trivially satisfied by total retrieval failure, so it cannot tell "the corpus
knows it does not have this" from "we have no idea whether it has this". That
is the same class of defect this harness was built to kill: the pre-existing
eval scored 50/50 errors and exited 0.

So a negative is ``absent`` only when BOTH hold:

1. **No gold signal matched** — no exact ``relevant_doc_ids`` hit and no
   ``match_terms`` substring anywhere in the top-k, exactly as for a miss; and
2. **The retriever demonstrably WORKED** — it returned at least
   ``MIN_RETRIEVED_FOR_ABSENCE`` documents. A healthy embedder always returns
   the k nearest neighbours for *any* string; returning fewer than that means
   the lane is down, the collection is wrong, or the retriever is dead. Those
   are errors, not evidence of absence.

Condition 2 is the whole point and it is why the negative is evaluable at all:
absence is a claim about the CORPUS, so it can only be established by a search
that actually ran. ``MIN_RETRIEVED_FOR_ABSENCE`` defaults to 5 — half of the
default ``top_k=10`` — deliberately below it so a slightly short result page is
not punished, and deliberately well above 0 so an empty one is not rewarded.

A negative that matches gold is graded like any other row (``gold``/``partial``
/``miss``): a real hit on the absent vocabulary is a false positive, which is
the loud failure this rule exists to surface, and it must NOT be laundered into
``absent``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Grade constants (kept as plain strings so they serialise into JSON reports).
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


def grade_absence(
    retrieved: list[dict[str, Any]],
    relevant_doc_ids: list[str],
    match_terms: list[str],
    partial_terms: list[str] | None = None,
    min_retrieved: int = MIN_RETRIEVED_FOR_ABSENCE,
) -> Grade:
    """Case-level grade for a NEGATIVE case (correct answer is "not covered").

    Args:
        retrieved: the docs the retriever returned, in rank order.
        relevant_doc_ids: gold ids — a negative carries none, but if one ever
            did it must be honoured, so it is still checked.
        match_terms: the ABSENT vocabulary. A hit on any of these is a false
            positive and is graded as one, never laundered into ``absent``.
        partial_terms: right-family terms, also treated as a false positive on
            a negative row.
        min_retrieved: the result-page floor. Below it, the search cannot be
            shown to have run, so absence is UNPROVEN and the grade is ``miss``.

    Returns:
        A single :class:`Grade` with ``rank=0`` (absence has no rank).

    Why this regrades internally rather than reusing the caller's grades: the
    positive path applies ``gold_rank_cap`` because a hit at rank 40 did not
    really answer the question. That reasoning is INVERTED here. On a negative
    the question is "does the forbidden vocabulary appear anywhere in what we
    handed back", and rank 6 is as much a false positive as rank 1 — it is in
    the result page the system produced. Passing the capped grades in was a
    live bug, caught by ``test_false_positive_is_not_laundered_into_absent``:
    with the cap, a genuine match at rank 9 was silently scored ``absent``.
    """
    # No cap: the whole retrieved page is in scope for a false positive.
    grades = grade_hits(
        retrieved,
        relevant_doc_ids,
        match_terms,
        gold_rank_cap=len(retrieved) or 1,
        partial_terms=partial_terms,
    )
    signal = [g for g in grades if g.grade in (GOLD, PARTIAL)]
    if signal:
        best = min(signal, key=lambda g: g.rank)
        return Grade(
            best.grade,
            best.rank,
            best.doc_id,
            f"false positive on a negative: {best.why}",
            best.matched_term,
        )
    n = len(retrieved)
    if n < min_retrieved:
        return Grade(
            MISS,
            0,
            "",
            f"absence UNPROVEN: {n} docs < min_retrieved={min_retrieved} (dead retriever looks identical to absent)",
        )
    return Grade(ABSENT, 0, "", f"no absent-vocabulary match in {n} retrieved docs")


__all__ = [
    "ABSENT",
    "GOLD",
    "MIN_RETRIEVED_FOR_ABSENCE",
    "MISS",
    "PARTIAL",
    "Grade",
    "grade_absence",
    "grade_hits",
]
