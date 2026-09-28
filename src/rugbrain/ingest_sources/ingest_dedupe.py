"""Near-duplicate detection for social ingest messages.

Syndication is the norm, not the exception: one wire story appears across five
channels, each with its own link, its own emoji and its own boilerplate. Stored
naively that is five documents that all look equally authoritative and all
answer the same query.

Exact normalised-hash matches are clustered first because they are cheap and
cannot produce a false positive. What remains is compared by token-shingle
Jaccard, which tolerates the light edits syndicators make (a leading emoji, a
changed ticker, a truncated sentence) without collapsing genuinely different
messages that merely share vocabulary.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

#: Token-shingle size for near-duplicate detection. Three tokens is the
#: smallest window that survives reordering inside a Telegram sentence.
SHINGLE_SIZE = 3

#: Jaccard similarity above which two messages are treated as the same story.
DUP_THRESHOLD = 0.62

#: Jaccard similarity above which two messages are treated as the same story.
DUP_THRESHOLD = 0.62

#: A representative's quality is the best member's score plus a corroboration
#: bonus; the bonus saturates so a spam loop cannot dominate.
CORROBORATION_CAP = 0.4
CORROBORATION_STEP = 0.15

#: Credit below this and a channel is reported for retirement.
CREDIT_FLOOR = 0.25

_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\U0000fe0f]+",
    flags=re.UNICODE,
)
_MENTION_RE = re.compile(r"@\w+")
_WS_RE = re.compile(r"\s+")
#: Tokens are alphanumeric runs only — punctuation must not survive into a
#: token or "drained 12M," and "drained 12m" become different stories.
_WORD_RE = re.compile(r"[a-z0-9$#]{2,}")


def normalize_for_dedupe(text: str) -> str:
    """Strip the parts that differ between syndications of one story.

    URLs, emoji, @mentions and whitespace carry no story identity: five
    outlets paste the same wire text with their own link and their own emoji.
    """
    stripped = _URL_RE.sub(" ", text or "")
    stripped = _EMOJI_RE.sub(" ", stripped)
    stripped = _MENTION_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", stripped).strip().lower()


def _tokenize(normalized: str) -> list[str]:
    """Alphanumeric tokens, punctuation discarded."""
    return _WORD_RE.findall(normalized)


def shingles(normalized: str, size: int = SHINGLE_SIZE) -> set[tuple[str, ...]]:
    """Token shingles for near-duplicate similarity.

    Punctuation is dropped here rather than in `normalize_for_dedupe` so the
    normalised string stays readable for debugging.
    """
    tokens = _tokenize(normalized)
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(left: set[tuple[str, ...]], right: set[tuple[str, ...]]) -> float:
    """Jaccard similarity of two shingle sets."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if not intersection:
        return 0.0
    return intersection / len(left | right)


def cluster_duplicates(messages: list[Any], *, threshold: float = DUP_THRESHOLD) -> list[list[Any]]:
    """Group near-identical messages that arrived in this batch.

    Exact normalised-hash matches seed a cluster (cheap, no false positives);
    everything else is compared by shingle Jaccard against the clusters built
    so far. Every message lands in exactly one cluster.
    """
    def fingerprint_of(message: Any) -> set[tuple[str, ...]]:
        return shingles(normalize_for_dedupe(message.text))

    clusters: list[list[Any]] = []
    fingerprints: list[set[tuple[str, ...]]] = []
    by_hash: dict[str, list[Any]] = defaultdict(list)
    for message in messages:
        by_hash[normalize_for_dedupe(message.text)].append(message)

    def add(cluster: list[Any]) -> None:
        clusters.append(cluster)
        # The cluster's fingerprint is its RICHEST member's shingles. Tracked as
        # a parallel list rather than by overwriting clusters[i][0]: that member
        # is still a member, and replacing it duplicated the new message and
        # dropped the old one.
        fingerprints.append(fingerprint_of(max(cluster, key=lambda m: len(m.text))))

    # Only groups of 2+ are seeded here; singletons are the remainder below.
    # Seeding singletons too would place them in a cluster and then re-test
    # them as remainder, duplicating them across two clusters.
    seeded = sorted((g for g in by_hash.values() if len(g) > 1), key=len, reverse=True)
    for group in seeded:
        add(list(group))

    for group in by_hash.values():
        if len(group) != 1:
            continue
        message = group[0]
        fingerprint = fingerprint_of(message)
        for index, existing in enumerate(fingerprints):
            if jaccard(fingerprint, existing) >= threshold:
                clusters[index].append(message)
                if len(message.text) > len(clusters[index][0].text):
                    fingerprints[index] = fingerprint
                break
        else:
            add([message])
    return clusters
