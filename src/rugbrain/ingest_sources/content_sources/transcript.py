"""Caption cues → time-stamped transcript chunks.

A transcript is only useful to retrieval if a hit can be traced to a moment in
the video, so every chunk keeps ``start``/``end`` seconds and a human timestamp.
Chunking is by wall-clock window (default two minutes) and a hard character cap,
which keeps a chunk inside the embedding budget without splitting a sentence
mid-cue.
"""

from __future__ import annotations

from typing import Any

__all__ = ["chunk_transcript"]

_WINDOW_S = 120.0
_MAX_CHARS = 1800


def _clock(seconds: float) -> str:
    """Seconds → ``H:MM:SS`` (always hours, so strings sort correctly)."""
    total = max(0, int(seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _cue(cue: dict[str, Any]) -> tuple[str, float, float] | None:
    """Normalise one caption cue; empty text means the cue carries no signal."""
    text = str(cue.get("text") or "").strip()
    if not text:
        return None
    start = float(cue.get("start") or 0.0)
    return text, start, float(cue.get("end") or start)


def _fits(current: dict[str, Any], end: float, length: int, window_s: float, cap: int) -> bool:
    """Can this cue join the open chunk without breaking time or size limits?"""
    return end - current["start"] <= window_s and len(current["text"]) + length <= cap


def chunk_transcript(
    cues: list[dict[str, Any]], *, window_s: float = _WINDOW_S, max_chars: int = _MAX_CHARS
) -> list[dict[str, Any]]:
    """Group timed caption cues into embedding-sized, timestamped chunks.

    A cue that would overflow the window OPENS the next chunk, so no second of
    speech is lost between windows. Ordering is cue order — captions arrive
    sorted, and re-sorting would cost more than it buys.
    """
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for cue in cues:
        parsed = _cue(cue)
        if parsed is None:
            continue
        text, start, end = parsed
        if current is not None and not _fits(current, end, len(text), window_s, max_chars):
            current["start_ts"] = _clock(current["start"])
            current["end_ts"] = _clock(current["end"])
            chunks.append(current)
            current = None
        if current is None:
            current = {"start": start, "end": end, "text": text}
            continue
        current["end"] = max(current["end"], end)
        current["text"] = f"{current['text']} {text}"
    if current is not None:
        current["start_ts"] = _clock(current["start"])
        current["end_ts"] = _clock(current["end"])
        chunks.append(current)
    return chunks
