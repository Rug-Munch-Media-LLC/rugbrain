"""Quality gate, channel credit and cluster scoring for social ingest.

The Telegram path stored whatever it fetched: the 20,000-entry `seen_hashes`
ring saturated (anything older than the newest 20k was re-admitted), five
outlets produced five copies of one story, and nothing checked whether an item
was crypto news at all — 87% of `news_v2` ended up `severity: low` with
Persian and Russian war channels in it.

This module is the policy half of the intelligence stage the ingester was
missing; the mechanics live in :mod:`rugbrain.ingest_sources.ingest_dedupe`.
It runs once per cycle over freshly fetched messages and does three things:

1. **Gate** — `news_filter.classify_text` decides topical / crypto-general /
   noise. Noise is dropped before storage, so widening channel coverage
   multiplies signal instead of noise.
2. **Score** — a cluster's quality is the best member's importance plus a
   corroboration bonus that saturates, so a spam loop cannot dominate.
3. **Credit** — a per-channel credit that decays when a channel keeps
   producing noise, with retirement left to the caller.

Kept separate from the ingester so the policy is testable on its own and the
ingester module stays inside the 200-line limit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

from rugbrain.news_filter import classify_text

#: A representative's quality is the best member's score plus a corroboration
#: bonus; the bonus saturates so a spam loop cannot dominate.
CORROBORATION_CAP = 0.4
CORROBORATION_STEP = 0.15

#: Credit below this and a channel is reported for retirement.
CREDIT_FLOOR = 0.25


class Scored(Protocol):
    """The subset of an ingested message this module needs."""

    text: str
    channel_title: str
    category: str
    c_hash: str


def gate(
    messages: list[Any], *, min_verdict: str = "crypto-general"
) -> tuple[list[Any], dict[str, int]]:
    """Drop non-crypto messages. Returns (kept, verdict_counts).

    ``min_verdict`` is one of ``topical``, ``crypto-general`` or ``noise``;
    anything strictly below it is dropped. A known crypto category
    (``security``, ``scam_caller``, ``defi`` …) is topical regardless of body
    text, which is what keeps a bare alert from a security firm in.
    """
    order = {"noise": 0, "crypto-general": 1, "topical": 2}
    floor = order.get(min_verdict, 1)
    kept: list[Any] = []
    counts: dict[str, int] = defaultdict(int)
    for message in messages:
        verdict = classify_text(message.category, message.text)
        counts[verdict] += 1
        if order.get(verdict, 0) >= floor:
            kept.append(message)
    return kept, dict(counts)


def representative_score(
    cluster: list[Any],
    *,
    base_importance: float,
    channel_credit: dict[str, float],
) -> float:
    """Quality score for a cluster: best importance plus corroboration bonus.

    Corroboration counts DISTINCT channels, not messages, so one channel
    repeating itself five times earns nothing.
    """
    distinct = {m.channel_title for m in cluster}
    bonus = min(CORROBORATION_CAP, CORROBORATION_STEP * (len(distinct) - 1))
    credit = sum(channel_credit.get(name, 0.5) for name in distinct) / max(len(distinct), 1)
    return round(base_importance * (1.0 + bonus) * (0.5 + 0.5 * credit), 4)


def channel_hit_rates(messages: list[Any], kept: list[Any]) -> dict[str, float]:
    """Per-channel kept/total ratio for this batch, in [0, 1]."""
    total: dict[str, int] = defaultdict(int)
    for message in messages:
        total[message.channel_title] += 1
    kept_names: dict[str, int] = defaultdict(int)
    for message in kept:
        kept_names[message.channel_title] += 1
    return {name: kept_names.get(name, 0) / count for name, count in total.items() if count}


def update_channel_credit(
    credit: dict[str, float],
    hit_rates: dict[str, float],
    *,
    decay: float = 0.02,
) -> dict[str, float]:
    """Blend this batch's per-channel hit rate into a slowly decaying credit.

    A channel that produces mostly noise trends toward 0 (and so falls under
    ``CREDIT_FLOOR``); one that produces real crypto items trends toward 1.
    Only channels present in ``hit_rates`` move, so a channel absent from this
    cycle keeps its credit — deciding retirement is the caller's job, not this
    function's.
    """
    blended = dict(credit)
    for name, rate in hit_rates.items():
        current = blended.get(name, 0.5)
        blended[name] = round(max(0.0, min(1.0, current * (1.0 - decay) + rate * decay)), 4)
    return blended


def channels_to_retire(credit: dict[str, float], *, floor: float = CREDIT_FLOOR) -> list[str]:
    """Channels whose credit has decayed under the floor, worst first."""
    low = {name: value for name, value in credit.items() if value < floor}
    return sorted(low, key=lambda name: low[name])


@dataclass
class StageResult:
    """Outcome of one pass of the intelligence stage."""

    kept: list[Any]
    gated_out: int
    clusters: int
    collapsed: int
    gate: dict[str, int]
    credit: dict[str, float]
    retiring: list[str]


def run_stage(
    messages: list[Any],
    *,
    seen_hashes: set[str],
    credit: dict[str, float] | None = None,
) -> StageResult:
    """Gate, cluster and score one cycle's messages, and update channel credit.

    This is the single call the ingester makes, so the policy stays in one
    place and the ingester does not grow. ``seen_hashes`` is the already-stored
    set: a message whose hash is present was ingested in an earlier cycle and
    is dropped here rather than re-embedded.
    """
    from rugbrain.ingest_sources.ingest_dedupe import cluster_duplicates

    fresh = [m for m in messages if m.c_hash not in seen_hashes]
    kept, verdicts = gate(fresh)
    clusters = cluster_duplicates(kept)
    merged = update_channel_credit(credit or {}, channel_hit_rates(fresh, kept))
    return StageResult(
        kept=kept,
        gated_out=len(fresh) - len(kept),
        clusters=len(clusters),
        collapsed=len(kept) - len(clusters),
        gate=verdicts,
        credit=merged,
        retiring=channels_to_retire(merged),
    )
