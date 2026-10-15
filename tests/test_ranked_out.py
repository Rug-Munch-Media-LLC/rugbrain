"""Guard the ranked-out diagnosis: the 5 covered-but-missed rows stay documented.

The eval must not lie. A row that is COVERED (its anchor exists) but misses at
top-5 is a different failure from an orphan, and the honest response is to
record WHY — never to re-anchor a correct chunk to a wrong one to make a number
go green. This test locks the diagnosis in place and asserts each entry carries
the fields a reader needs to act on it.
"""

from __future__ import annotations

from rugbrain.eval.ranked_out import RANKED_OUT

#: The five rows the retrieval run flagged as covered-but-missed (2026-10-15).
EXPECTED_QUERIES = {
    "zk circuit underconstrained proof vulnerability",
    "MEV sandwich attack prevention",
    "oracle price manipulation flash loan lending",
    "honeypot token detected risk flags scanner",
    "mev sandwich attack front run back run",
}


def test_all_five_missed_rows_are_documented() -> None:
    assert set(RANKED_OUT) == EXPECTED_QUERIES


def test_every_entry_names_a_collection_and_a_cause() -> None:
    for query, info in RANKED_OUT.items():
        assert info["collection"], f"{query!r} has no collection"
        assert info["cause"], f"{query!r} has no cause"
        assert info["detail"], f"{query!r} has no detail"
        # rank is either an int (measured) or None (not in top-500) — never a
        # string, so a reader can compare it numerically.
        assert info["rank"] is None or isinstance(info["rank"], int), query


def test_mis_anchored_row_is_flagged_not_silently_re_anchored() -> None:
    """The honeypot row is a mis-anchor; the diagnosis must say so explicitly.

    This is the row most likely to be "fixed" by pointing it at a different
    chunk. The diagnosis must keep the distinction visible so that fix is a
    deliberate decision, not a silent green.
    """
    info = RANKED_OUT["honeypot token detected risk flags scanner"]
    assert "mis-anchored" in str(info["cause"])
    assert "re-anchor" in str(info["detail"]).lower()
