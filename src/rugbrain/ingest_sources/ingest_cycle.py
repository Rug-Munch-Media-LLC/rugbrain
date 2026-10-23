"""Post-fetch processing for a Telegram ingest cycle.

Everything that happens to a cycle's messages AFTER they are fetched lives
here, so the ingester module neither grows nor has to know the policy:

    fetch -> echo counts -> intelligence stage -> documents

The intelligence stage (gate, near-duplicate clustering, scoring, per-channel
credit) is in :mod:`rugbrain.ingest_sources.ingest_quality`; this module only
sequences it and returns what the caller needs to report and persist.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Protocol

from rugbrain.ingest_sources.ingest_quality import run_stage

#: Bound on the persisted seen-hash ring. The old bound of 20,000 was
#: saturated in production, which meant every hash older than the newest 20k
#: was forgotten and re-admitted on the next cycle.
MAX_SEEN_HASHES = 50_000


class Fetched(Protocol):
    """A fetched message, as the ingester models it."""

    c_hash: str
    text: str
    channel_title: str
    category: str


def process_cycle(
    messages: list[Fetched],
    *,
    seen_hashes: list[str],
    credit: dict[str, float] | None = None,
    max_seen: int = MAX_SEEN_HASHES,
) -> tuple[list[Any], dict[str, float], dict[str, Any]]:
    """Run one cycle's intelligence stage and return (kept, credit, stats).

    ``seen_hashes`` is replaced, not extended in place: it is re-bounded to
    ``max_seen`` newest entries so the ring cannot silently saturate again.
    """
    stage = run_stage(messages, seen_hashes=set(seen_hashes), credit=credit or {})
    # Keep insertion order and drop the OLDEST overflow. A set slice is invalid
    # and a plain set has no order, which would evict arbitrary hashes rather
    # than the stale ones.
    merged = list(deque(seen_hashes, maxlen=max_seen))
    merged.extend(h for h in (m.c_hash for m in messages) if h not in set(merged))
    retained = merged[-max_seen:]
    stats: dict[str, Any] = {
        "messages": len(messages),
        "gated_out": stage.gated_out,
        "gate": stage.gate,
        "clusters": stage.clusters,
        "collapsed": stage.collapsed,
    }
    if stage.retiring:
        stats["channels_to_retire"] = stage.retiring[:20]
    return stage.kept, stage.credit, {"seen_hashes": list(retained), **stats}


def echo_counts(messages: list[Fetched]) -> dict[str, int]:
    """How many messages share each content hash — the cross-channel signal."""
    counts: dict[str, int] = defaultdict(int)
    for message in messages:
        counts[message.c_hash] += 1
    return counts


def gate_and_bound(
    items: list[Any],
    *,
    seen_hashes: list[str],
    get_text: Any,
    get_category: Any,
    get_hash: Any,
    max_seen: int = MAX_SEEN_HASHES,
) -> tuple[list[Any], list[str], dict[str, Any]]:
    """Drop already-seen items, bound the ring, and apply the crypto gate.

    Shared by the userbot and the t.me/s web path so both get the same policy
    and the same ring bound. The earlier code kept the ring unbounded, which is
    how it saturated at 20,000 and began re-admitting old items every cycle.

    Dedupe is per-cycle as well as per-ring. Building ``known`` from the
    previous ring only meant the same text mirrored by five shill channels in
    one cycle passed five times and cost five vector calls; the hash is
    content-only (no channel in it), so collapsing on it IS the dedupe across
    channels.
    """
    from rugbrain.ingest_sources.ingest_quality import gate_items

    known = set(seen_hashes)
    fresh: list[Any] = []
    for item in items:
        item_hash = get_hash(item)
        if item_hash in known:
            continue
        known.add(item_hash)
        fresh.append(item)
    bounded = list(deque(seen_hashes, maxlen=max_seen))
    bounded.extend(h for h in (get_hash(i) for i in fresh) if h not in set(bounded))
    pre_gate = len(fresh)
    kept, verdicts = gate_items(fresh, get_text=get_text, get_category=get_category)
    return (
        kept,
        bounded[-max_seen:],
        {
            "gated_out": pre_gate - len(kept),
            "cross_channel_duplicates": len(items) - len(fresh),
            "gate": verdicts,
        },
    )
