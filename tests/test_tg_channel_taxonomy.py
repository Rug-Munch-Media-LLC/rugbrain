"""Taxonomy tests for the Telegram userbot ingester (2026-09-28).

Regression cover for the live mislabels found with 219 dialogs:
``"eth"`` substring-matched unrelated words (``health`` → ``market``) and the
call channels (``Catfish Calls by Poe``, ``Milady Calls``, ``Dior's Alpha``,
``Marcell's Trenches``) fell inconsistently into ``shiller``/``general``.

The **emitted** category strings are a closed contract: ``news_filter`` is the
single classifier shared by the ingest gate and the noise-cleanup script, and an
unrecognised category silently demotes a doc to ``noise``. Hence
``alpha_caller``/``research`` are aliased onto ``shiller``/``news``.
"""

from __future__ import annotations

import pytest

from rugbrain.ingest_sources.channel_taxonomy import (
    CATEGORY_ALIASES,
    CATEGORY_RULES,
    CATEGORY_WEIGHT,
    categorize,
)

#: Every value news_filter ranks as "topical" (subset that we can emit).
EMITTABLE = frozenset(CATEGORY_WEIGHT) & frozenset(CATEGORY_ALIASES.values())


@pytest.mark.parametrize(
    ("title", "username", "expected"),
    [
        # ── the reported mislabels ──────────────────────────────────────
        ("Catfish Calls by Poe", "CatfishcallsbyPoe", "shiller"),
        ("Milady Calls", "MiladyCalls", "shiller"),
        ("Sarah low cap gambles", "Sarahlowcap", "shiller"),
        ("Dior's Alpha", "DiorsAlpha", "shiller"),
        ("Marcell's Trenches", "MarcellsTrenches", "shiller"),
        # ── the "eth" substring bug ─────────────────────────────────────
        ("Something Health News", "healthnews", "news"),
        ("Clothing Outlet Deals", "outletdeals", "general"),
        # ── security beats generic words ───────────────────────────────
        ("PeckShieldAlert", "PeckShield", "security"),
        ("BlockSec Alert", "BlockSec", "security"),
        ("Certik Alert", "certik_alert", "security"),
        ("SlowMist Exploit Recap", "slowmist", "security"),
        ("Immunefi Bounty Board", "immunefi", "security"),
        # ── scam callers ───────────────────────────────────────────────
        ("Honeypot Scanner", "honeypots", "scam_caller"),
        ("Scam DB", "scamdb", "scam_caller"),
        ("Scam Reports Daily", "scamreports", "scam_caller"),
        ("Rug Alert Feed", "rugalert", "scam_caller"),
        # ── data / market / news ───────────────────────────────────────
        ("Nansen Whale Alerts", "nansen", "data"),
        ("On-Chain Data Daily", "onchain", "data"),
        ("DeFiLlama TVL", "defillama", "data"),
        ("BTC Price Talk", "btcprice", "market"),
        ("Perps Signals", "perpsignals", "market"),
        ("Blockchain News", "blockchainnews", "news"),
        ("The Daily Press", "dailypress", "news"),
        ("WatcherGuru", "watcherguru", "news"),
        ("WatcherGuru Chat", "watcherguruchat", "news"),
        ("News Crypto", "news_crypto", "news"),
        # ── brands win over ticker words ───────────────────────────────
        ("screamAI", "screamai", "data"),
    ],
)
def test_categorize_cases(title: str, username: str, expected: str) -> None:
    assert categorize(title, username) == expected


@pytest.mark.parametrize(
    ("title", "username"),
    [
        ("", ""),
        ("The Block", "theblock"),
        ("Immunity (sec)", "immunity"),
        ("BridgeLlama", "bridgellama"),
        ("Just Chatting", "chatting"),
        ("X", ""),
    ],
)
def test_longtail_fallback(title: str, username: str) -> None:
    assert categorize(title, username) == "general"


def test_alpha_and_research_alias_onto_existing_values() -> None:
    """New rule names must not become new payload categories."""
    assert CATEGORY_ALIASES["alpha_caller"] == "shiller"
    assert CATEGORY_ALIASES["research"] == "news"
    assert categorize("Low Cap Gems", "lowcapgems") == "shiller"
    assert categorize("MEV Research", "mevresearch") == "news"


def test_every_rule_category_is_aliasable() -> None:
    """No rule may emit a value outside the news_filter contract."""
    for cat, _ in CATEGORY_RULES:
        assert cat in CATEGORY_ALIASES or cat in CATEGORY_WEIGHT, f"unmapped rule: {cat}"


def test_needles_are_word_bounded() -> None:
    """No substring needles — every needle is tokenised and matched as a word.

    This is the ``"eth"`` bug class. The literal rule set keeps a 4-char floor
    (``gem``/``btc``/``10x`` are deliberate chain/gain words, safe *because*
    matching is whole-word) and drops the genuinely lossy short ones (``rug``,
    ``ta``) in favour of explicit phrases (``rug alert``).
    """
    for _cat, needles in CATEGORY_RULES:
        for needle in needles:
            words = needle.split(" ")
            assert all(w.isalnum() for w in words), f"punctuation in needle: {needle!r}"
            assert min(len(w) for w in words) >= 2, f"loose needle: {needle!r}"
            assert needle not in ("rug", "ta", "eth"), f"substring needle: {needle!r}"
            # Single words are matched as whole tokens; only multi-word needles
            # are allowed to be phrase substrings (on the token-joined blob).
            if len(words) == 1:
                assert len(needle) >= 3, f"loose needle: {needle!r}"


def test_public_signature_preserved() -> None:
    from rugbrain.ingest_sources.telegram_userbot import categorize as via_userbot

    assert via_userbot("Milady Calls", "MiladyCalls") == "shiller"
