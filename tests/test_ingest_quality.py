"""Tests for the social-ingest intelligence stage.

Covers the three things the Telegram path previously did not do: gate
non-crypto traffic, collapse syndicated duplicates, and let a channel's
reliability decay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from rugbrain.ingest_sources.ingest_dedupe import (
    DUP_THRESHOLD,
    cluster_duplicates,
    jaccard,
    normalize_for_dedupe,
    shingles,
)
from rugbrain.ingest_sources.ingest_quality import (
    channel_hit_rates,
    channels_to_retire,
    gate,
    representative_score,
    update_channel_credit,
)


@dataclass
class Msg:
    text: str
    channel_title: str
    category: str = "security"
    c_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.c_hash:
            self.c_hash = str(abs(hash(self.text)))


EXPLOIT = "HackenProof: BLUR exploit drained 12M, attacker bridged funds to Tornado"
EXPLOIT_VARIANT = "HackenProof: BLUR exploit drained 12M, attacker bridged funds to Tornado!!"
EXPLOIT_OTHER = "SlowMist: BLUR exploit drained 12M, attacker bridged funds to Tornado"
UNRELATED = "Breaking: Bitcoin ETF approved by SEC, spot inflows 400M today"
FOREIGN_NEWS = "Погибшие в результате атаки в городе, сообщают очевидцы"


# ── normalisation ─────────────────────────────────────────────────────


def test_normalize_strips_links_emoji_and_mentions() -> None:
    raw = "🚨 GM @slowmist: exploit confirmed https://t.me/slowmist_alerts"
    normalized = normalize_for_dedupe(raw)
    assert "http" not in normalized
    assert "@slowmist" not in normalized
    assert "🚨" not in normalized


def test_jaccard_is_one_for_punctuation_variants_and_zero_for_disjoint() -> None:
    left = shingles(normalize_for_dedupe(EXPLOIT))
    variant = shingles(normalize_for_dedupe(EXPLOIT_VARIANT))
    other_channel = shingles(normalize_for_dedupe(EXPLOIT_OTHER))
    unrelated = shingles(normalize_for_dedupe(UNRELATED))
    # Punctuation-only edits are the same story, not merely similar.
    assert jaccard(left, variant) == 1.0
    # A different firm's byline lowers overlap but must still cluster.
    assert DUP_THRESHOLD <= jaccard(left, other_channel) < 1.0
    assert jaccard(left, unrelated) == 0.0


# ── clustering ────────────────────────────────────────────────────────


def test_syndicated_copies_collapse_into_one_cluster() -> None:
    messages = [
        Msg(EXPLOIT, "HackenProof"),
        Msg(EXPLOIT_OTHER, "SlowMist"),
        Msg(EXPLOIT_VARIANT, "BlockSec"),
        Msg(UNRELATED, "CryptoNews"),
    ]
    clusters = cluster_duplicates(messages)
    assert len(clusters) == 2
    biggest = max(clusters, key=len)
    assert {m.channel_title for m in biggest} == {"HackenProof", "SlowMist", "BlockSec"}


def test_every_message_lands_in_exactly_one_cluster() -> None:
    """Regression: singletons were once seeded AND re-tested as remainder,
    so the same message appeared in two clusters."""
    messages = [
        Msg(EXPLOIT, "HackenProof"),
        Msg(EXPLOIT_OTHER, "SlowMist"),
        Msg(EXPLOIT_VARIANT, "BlockSec"),
        Msg(UNRELATED, "CryptoNews"),
        Msg(FOREIGN_NEWS, "SomeWarChannel"),
    ]
    clusters = cluster_duplicates(messages)
    flat = [id(m) for group in clusters for m in group]
    assert sorted(flat) == sorted(id(m) for m in messages)


def test_exact_duplicates_cluster_without_similarity() -> None:
    messages = [Msg(EXPLOIT, "A"), Msg(EXPLOIT, "B"), Msg(EXPLOIT, "C")]
    clusters = cluster_duplicates(messages)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


# ── gate ──────────────────────────────────────────────────────────────


def test_gate_keeps_crypto_and_drops_foreign_noise() -> None:
    messages = [
        Msg(EXPLOIT, "HackenProof", category="security"),
        Msg(UNRELATED, "CryptoNews", category="news"),
        Msg(FOREIGN_NEWS, "WarChannel", category="general"),
    ]
    kept, counts = gate(messages)
    assert {m.channel_title for m in kept} == {"HackenProof", "CryptoNews"}
    assert counts.get("noise", 0) >= 1


def test_gate_keeps_known_crypto_category_even_on_thin_text() -> None:
    """A security firm posting a bare alert must survive the gate."""
    messages = [Msg("alert", "HackenProof", category="security")]
    kept, _ = gate(messages)
    assert len(kept) == 1


def test_gate_can_require_topical() -> None:
    messages = [Msg(UNRELATED, "CryptoNews", category="general")]
    kept, _ = gate(messages, min_verdict="topical")
    assert kept == []


# ── scoring ───────────────────────────────────────────────────────────


def test_corroboration_from_distinct_channels_raises_quality() -> None:
    single = [Msg(EXPLOIT, "HackenProof")]
    many = [Msg(EXPLOIT, "HackenProof"), Msg(EXPLOIT, "SlowMist"), Msg(EXPLOIT, "BlockSec")]
    credit = {"HackenProof": 1.0, "SlowMist": 1.0, "BlockSec": 1.0}
    one = representative_score(single, base_importance=2.0, channel_credit=credit)
    three = representative_score(many, base_importance=2.0, channel_credit=credit)
    assert three > one


def test_one_channel_repeating_itself_earns_no_corroboration() -> None:
    repeated = [Msg(EXPLOIT, "HackenProof")] * 4
    credit = {"HackenProof": 1.0}
    assert representative_score(
        repeated, base_importance=2.0, channel_credit=credit
    ) == representative_score(repeated[:1], base_importance=2.0, channel_credit=credit)


# ── channel credit ────────────────────────────────────────────────────


def test_hit_rates_are_per_channel() -> None:
    all_msgs = [
        Msg(EXPLOIT, "Good", category="security"),
        Msg(FOREIGN_NEWS, "Bad", category="general"),
        Msg(UNRELATED, "Bad", category="general"),
    ]
    kept = [all_msgs[0]]
    rates = channel_hit_rates(all_msgs, kept)
    assert rates == {"Good": 1.0, "Bad": 0.0}


def test_noise_channel_credit_decays_and_is_flagged_for_retirement() -> None:
    credit = {"Noisy": 0.9, "Useful": 0.9}
    # 0.9 * 0.98**60 is still ~0.27, above the floor; 120 cycles is ~0.06.
    for _ in range(120):
        credit = update_channel_credit(credit, {"Noisy": 0.0, "Useful": 1.0})
    assert credit["Noisy"] < 0.25
    assert credit["Useful"] > 0.8
    assert channels_to_retire(credit) == ["Noisy"]


def test_credit_ignores_channels_absent_from_the_batch() -> None:
    credit = {"Quiet": 0.8}
    updated = update_channel_credit(credit, {"Other": 1.0})
    assert updated["Quiet"] == 0.8


@pytest.mark.parametrize("rate,expected_direction", [(0.0, "down"), (1.0, "up")])
def test_credit_moves_in_the_direction_of_the_hit_rate(
    rate: float, expected_direction: str
) -> None:
    start = 0.5
    moved = update_channel_credit({"C": start}, {"C": rate}, decay=0.5)["C"]
    if expected_direction == "down":
        assert moved < start
    else:
        assert moved > start
