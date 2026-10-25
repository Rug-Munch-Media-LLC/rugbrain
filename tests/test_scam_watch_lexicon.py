"""Lexicon matching is boundary-aware and disclosure is a separate question."""

from __future__ import annotations

import pytest

from rugbrain.scam_watch.lexicon import (
    GUARANTEE_TERMS,
    PRESALE_TERMS,
    URGENCY_TERMS,
    find_terms,
    has_disclosure,
)


@pytest.mark.parametrize(
    "text",
    ["guaranteed profits today", "this is RISK FREE", "easy 100x", "to the moon"],
)
def test_guarantee_terms_hit(text):
    assert find_terms(text, GUARANTEE_TERMS)


def test_word_boundary_prevents_partial_matches():
    # "guaranteed" is not inside "unguaranteed"? It is a suffix; boundaries stop it.
    assert find_terms("unguaranteed nonsense", GUARANTEE_TERMS) == ()
    assert find_terms("presales are boring", PRESALE_TERMS) == ()


def test_phrases_allow_flexible_whitespace():
    assert find_terms("last   chance to buy", URGENCY_TERMS) == ("last chance",)


def test_disclosure_label_counts_without_text():
    assert has_disclosure("mint abc", paid_promo_label=True) is True


@pytest.mark.parametrize(
    "text", ["this video is sponsored by X", "paid promotion", "#ad", "#sponsored"]
)
def test_in_text_disclosures_are_recognised(text):
    assert has_disclosure(text) is True


def test_plain_hype_text_is_undisclosed():
    assert has_disclosure("guaranteed 100x, join telegram") is False
