"""What we already posted — cross-day repeat detection and per-day counts.

In-batch dedupe (:mod:`rugbrain.content_dedupe`) can only see this cycle. The
failures that hurt are the ones it cannot: the same story resurfacing a day
later under a new URL, and the same item landing in two channels on one day.
Both need a record of what was published, so this module owns that record and
nothing else.

Identity is the SAME three layers, tried cheapest first, so a story that comes
back with a fresh link and one edited sentence is caught by the layer that can
see it:

    exact     SHA-256 of the normalised title+body
    near      token-shingle Jaccard >= DUP_THRESHOLD
    semantic  corpus-lane cosine >= SEMANTIC_THRESHOLD (only if a vector is given)

Vectors are optional on purpose: the ledger stays cheap to populate from a
publisher, which usually has the identity and not the embedding. Pass them when
the pipeline has them and the semantic layer joins in.

No I/O and no clock: the caller supplies ``day`` (``YYYY-MM-DD``) and the vector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rugbrain.content_dedupe_layers import (
    EXACT,
    NEAR,
    NEAR_THRESHOLD,
    SEMANTIC,
    SEMANTIC_THRESHOLD,
    cosine,
    exact_key,
    story_text,
)
from rugbrain.ingest_sources.ingest_dedupe import jaccard, shingles

#: How many days of history to keep. Cross-day dedupe is about "the same story
#: resurfacing", which in practice means days, not months.
RETENTION_DAYS: int = 14


@dataclass(frozen=True)
class Seen:
    """One published item, in the (cheap) shape identity needs."""

    id: str
    channel: str
    day: str
    exact: str
    text: str
    vector: tuple[float, ...] | None = None


@dataclass
class SeenLedger:
    """Published-item memory with per-channel, per-day counts."""

    retention_days: int = RETENTION_DAYS
    entries: list[Seen] = field(default_factory=list)

    def remember(
        self,
        story: dict[str, Any],
        *,
        channel: str,
        day: str,
        vector: list[float] | None = None,
    ) -> Seen:
        """Record a published story. The day is the caller's, not the clock's."""
        _ = self._prune(day)
        entry = Seen(
            id=str(story.get("id") or ""),
            channel=channel,
            day=day,
            exact=exact_key(story),
            text=story_text(story),
            vector=tuple(vector) if vector else None,
        )
        self.entries.append(entry)
        return entry

    def repeat_of(
        self, story: dict[str, Any], *, vector: list[float] | None = None
    ) -> dict[str, Any] | None:
        """The strongest evidence this story was already published, or None.

        Returns ``{"layer": ..., "entry": Seen, "similarity": float}``. Layers
        are tried cheapest first, so the answer names the WEAKEST layer that was
        sufficient rather than the most flattering one.
        """
        key = exact_key(story)
        text = story_text(story)
        fingerprint = shingles(text)
        for entry in reversed(self.entries):
            if entry.exact == key:
                return {"layer": EXACT, "entry": entry, "similarity": 1.0}
        best: dict[str, Any] | None = None
        best_score = 0.0
        for entry in reversed(self.entries):
            score = jaccard(fingerprint, shingles(entry.text))
            if score >= NEAR_THRESHOLD and score > best_score:
                best, best_score = {"layer": NEAR, "entry": entry, "similarity": score}, score
        if best is not None:
            return best
        for entry in reversed(self.entries):
            if vector is None or entry.vector is None:
                continue
            score = cosine(list(vector), list(entry.vector))
            if score >= SEMANTIC_THRESHOLD and score > best_score:
                best, best_score = {"layer": SEMANTIC, "entry": entry, "similarity": score}, score
        return best

    def posted(self, channel: str, day: str) -> int:
        """How many items this channel already published on ``day``."""
        return sum(1 for entry in self.entries if entry.channel == channel and entry.day == day)

    def _prune(self, day: str) -> int:
        """Drop entries older than the retention window; returns how many."""
        try:
            cutoff = _minus_days(day, self.retention_days)
        except ValueError:
            return 0
        keep = [entry for entry in self.entries if entry.day >= cutoff]
        dropped = len(self.entries) - len(keep)
        self.entries = keep
        return dropped


def _minus_days(day: str, days: int) -> str:
    """``YYYY-MM-DD`` minus *days*, ISO again (lexicographic order == time order)."""
    from datetime import date, timedelta

    parsed = date.fromisoformat(day)
    return (parsed - timedelta(days=days)).isoformat()


__all__ = ["RETENTION_DAYS", "Seen", "SeenLedger"]
