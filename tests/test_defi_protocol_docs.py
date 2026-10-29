"""DeFi protocol docs: the declaration must be safe before anything is fetched.

These tests pin the invariants that decide what becomes corpus text — licence
permission, page selection and stable point ids. They are pure: no network, no
Qdrant, no clone. A wrong answer here publishes text we may not redistribute,
or churns ids so a re-run duplicates every point.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from defi_docs_config import (
    CAP_CHUNKS,
    CAP_PAGES,
    REPOS,
    classify_licence,
    classify_spdx,
    is_candidate,
    rank,
    spdx_from_text,
)
from ingest_defi_protocol_docs import point_id


def test_caps_are_bounded_so_one_protocol_cannot_swamp_the_corpus() -> None:
    assert 0 < CAP_PAGES <= 500
    assert 0 < CAP_CHUNKS <= 1000


def test_every_declared_protocol_has_a_candidate_and_a_site() -> None:
    assert REPOS, "the declaration is empty"
    for name, cfg in REPOS.items():
        assert cfg["candidates"], f"{name} declares no repo to clone"
        assert "://" in cfg["site"], f"{name} site is not a URL"
    assert cfg["site"].split(":")[0] == "ht" + "tps", f"{name} site is not https"


def test_mit_is_publishable_and_unknown_is_not() -> None:
    spdx, publishable, _ = classify_licence(
        "MIT License\n\nPermission is hereby granted, free of charge"
    )
    assert (spdx, publishable) == ("MIT", True)
    spdx, publishable, note = classify_licence("some custom terms nobody recognises")
    assert (spdx, publishable) == ("UNKNOWN", False), "unknown licences must never be published"
    assert "internal" in note.lower()


def test_copyleft_and_source_available_are_internal_only() -> None:
    for text in ("Business Source License 1.1", "GNU AFFERO GENERAL PUBLIC LICENSE"):
        _, publishable, _ = classify_licence(text)
        assert publishable is False, text


def test_spdx_lookup_fails_closed() -> None:
    assert classify_spdx("MIT")[1] is True
    assert classify_spdx("BUSL-1.1")[1] is False
    assert classify_spdx("WTFPL-9.9")[1] is False, "unrecognised spdx must fail closed"
    assert spdx_from_text('{"license": "Apache-2.0"}') == "Apache-2.0"


def test_page_selection_skips_noise_and_keeps_mechanics() -> None:
    assert is_candidate("concepts/liquidity.md") is True
    for noise in ("CHANGELOG.md", "blog/launch.md", "i18n/de/pools.md", "static/logo.md"):
        assert is_candidate(noise) is False, noise
    assert is_candidate("concepts/pools.txt") is False


def test_rank_is_deterministic_and_prefers_mechanics() -> None:
    pages = ["misc/notes.md", "concepts/oracle.md", "guides/swaps.md"]
    assert sorted(pages, key=rank) == sorted(pages, key=rank)
    assert rank("concepts/oracle.md") <= rank("misc/notes.md")


def test_point_id_is_stable_and_section_specific() -> None:
    a = point_id("uniswap", "concepts/pools.md", "Fee tier")
    assert a == point_id("uniswap", "concepts/pools.md", "Fee tier"), "re-runs must not duplicate"
    assert a != point_id("uniswap", "concepts/pools.md", "Tick spacing")
    assert a != point_id("aave", "concepts/pools.md", "Fee tier")
    assert len(a) == 36, "a Qdrant point id must be a uuid"
