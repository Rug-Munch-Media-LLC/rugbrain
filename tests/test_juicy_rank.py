"""Ranking primitives: recency decay, severity, corroboration, redaction."""

from __future__ import annotations

import time

from rugbrain.intel import juicy_rank


def test_parse_timestamp_accepts_iso_epoch_and_junk() -> None:
    assert juicy_rank.parse_timestamp(1700000000) == 1700000000.0
    assert juicy_rank.parse_timestamp("1700000000") == 1700000000.0
    iso = juicy_rank.parse_timestamp("2023-11-14T22:13:20+00:00")
    assert iso == 1700000000.0
    assert juicy_rank.parse_timestamp("not-a-date") is None
    assert juicy_rank.parse_timestamp(None) is None


def test_recency_decays_by_half_life() -> None:
    now = 1_700_000_000.0
    assert juicy_rank.recency_score(now, now) == 1.0
    half = now - juicy_rank.HALF_LIFE_DAYS * 86400
    assert abs(juicy_rank.recency_score(half, now) - 0.5) < 1e-9
    assert juicy_rank.recency_score(None, now) == 0.3


def test_redaction_removes_paths_ips_and_secrets() -> None:
    raw = "see /root/notes.md at 192.0.2.9 key sk-abcdefghijklmnop"
    out = juicy_rank.redact(raw)
    assert "/srv/" not in out
    assert "192.0.2.9" not in out
    assert "sk-abcdefghijklmnop" not in out


def test_score_is_explainable_and_bounded() -> None:
    now = time.time()
    item = {
        "id": "p1",
        "collection": "exploits_8b",
        "kind": "exploit",
        "title": "Acme reentrancy exploit",
        "summary": "flash loan drain",
        "timestamp": now,
        "severity": "critical",
        "tags": ["exploit"],
    }
    ranked = juicy_rank.rank_items([item], now)
    assert len(ranked) == 1
    scores = ranked[0]["scores"]
    assert set(scores) == {"recency", "severity", "relevance", "corroboration"}
    assert 0.0 <= ranked[0]["score"] <= 1.0
    assert ranked[0]["source_count"] == 1


def test_corroboration_rewards_multiple_collections() -> None:
    now = time.time()
    base = {
        "kind": "hack",
        "title": "Grizzifi protocol logic flaw",
        "severity": "high",
        "timestamp": now,
    }
    single = juicy_rank.rank_items([{**base, "id": "a", "collection": "hacks_v2_8b"}], now)[0]
    pair = juicy_rank.rank_items(
        [
            {**base, "id": "a", "collection": "hacks_v2_8b"},
            {**base, "id": "b", "collection": "news_v2_8b"},
        ],
        now,
    )[0]
    assert pair["scores"]["corroboration"] > single["scores"]["corroboration"]
    assert pair["source_count"] == 2


def test_duplicate_point_ids_are_collapsed() -> None:
    now = time.time()
    item = {
        "id": "same",
        "collection": "scam_patterns_8b",
        "kind": "scam",
        "title": "phishing domain",
        "severity": "medium",
        "timestamp": now,
    }
    assert len(juicy_rank.rank_items([item, dict(item)], now)) == 1


def test_weights_fall_back_to_untuned_baseline(monkeypatch) -> None:
    monkeypatch.delenv(juicy_rank.WEIGHTS_ENV, raising=False)
    weights, source = juicy_rank.load_weights()
    assert weights == juicy_rank.BASELINE_WEIGHTS
    assert source == "baseline"
    assert sum(weights.values()) == 1.0


def test_private_weights_file_overrides_baseline(tmp_path, monkeypatch) -> None:
    path = tmp_path / "juicy_weights.json"
    path.write_text('{"recency": 0.5, "severity": 0.3, "relevance": 0.2, "corroboration": 0.0}')
    monkeypatch.setenv(juicy_rank.WEIGHTS_ENV, str(path))
    weights, source = juicy_rank.load_weights()
    assert weights == {"recency": 0.5, "severity": 0.3, "relevance": 0.2, "corroboration": 0.0}
    assert source == "private:juicy_weights.json"


def test_inline_and_invalid_weights_are_handled(monkeypatch) -> None:
    monkeypatch.setenv(
        juicy_rank.WEIGHTS_ENV, '{"recency": 2, "severity": 1, "relevance": 1, "corroboration": 0}'
    )
    weights, source = juicy_rank.load_weights()
    assert source == "private:inline"
    assert weights["recency"] == 0.5  # normalised to sum 1.0
    assert juicy_rank.load_weights('{"recency": 1}')[0] == juicy_rank.BASELINE_WEIGHTS


def test_rank_items_uses_supplied_weights() -> None:
    now = time.time()
    item = {
        "id": "w",
        "collection": "exploits_8b",
        "kind": "exploit",
        "title": "recency only",
        "severity": "low",
        "timestamp": now,
    }
    recency_only = {"recency": 1.0, "severity": 0.0, "corroboration": 0.0, "relevance": 0.0}
    assert juicy_rank.rank_items([item], now, weights=recency_only)[0]["score"] == 1.0
