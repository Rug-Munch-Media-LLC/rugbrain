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

from typing import Any

from rugbrain.missioneval.grading import (
    ABSENT,
    GOLD,
    MIN_RETRIEVED_FOR_ABSENCE,
    MISS,
    PARTIAL,
    Grade,
    grade_hits,
)


# Grade constants (kept as plain strings so they serialise into JSON reports).
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
