"""Query transform contracts (was 21% covered).

* expand_synonyms: alias clusters produce variants, word-boundary
  matching, max_variants cap, empty/garbage input safe
* _invoke_llm: sync + async callables both work; failures swallowed
  (expansion must never break retrieval)
"""

from __future__ import annotations

import pytest

from rugbrain.search.query_transform import (
    _MAX_SYNONYM_VARIANTS,
    _invoke_llm,
    expand_synonyms,
)


# ── expand_synonyms ───────────────────────────────────────────────────


def test_rugpull_alias_expands():
    out = expand_synonyms("was this a rugpull?")
    assert len(out) >= 2
    assert any("rug pull" in v for v in out[1:])


def test_canonical_term_also_expands():
    out = expand_synonyms("classic rug pull scam")
    assert any("rugpull" in v for v in out[1:])


def test_no_match_returns_original_only():
    assert expand_synonyms("completely unrelated query") == ["completely unrelated query"]


def test_empty_and_none_safe():
    assert expand_synonyms("") == [""]
    assert expand_synonyms("   ") == [""]


def test_max_variants_cap():
    # query hitting multiple clusters must not exceed the cap
    q = "rugpull flashloan sandwich multisig oracle manipulation"
    out = expand_synonyms(q, max_variants=3)
    assert len(out) <= 3


def test_word_boundary_no_substring_match():
    # "sandwiched" contains "sandwich" but not at a word boundary
    out = expand_synonyms("he sandwiched the trade")
    assert all("mev" not in v for v in out)


# ── _invoke_llm ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_llm_fn():
    assert await _invoke_llm(lambda q: f"expanded:{q}", "btc") == "expanded:btc"


@pytest.mark.asyncio
async def test_async_llm_fn():
    async def llm(q):
        return f"async:{q}"

    assert await _invoke_llm(llm, "eth") == "async:eth"


@pytest.mark.asyncio
async def test_llm_failure_swallowed():
    def boom(q):
        raise RuntimeError("llm down")

    assert await _invoke_llm(boom, "q") is None
    assert await _invoke_llm(None, "q") is None
