"""Persisted sweep state for the Telegram channel alt-ingest.

One job: remember the newest message id per channel so a re-run fetches only
new items instead of re-walking the whole history (and re-spending embed
calls on documents that are already stored).

The file is written atomically (tmp + ``os.replace``) so a truncated write can
never silently rewind a channel to zero and re-ingest its entire history.
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["AltIngestState", "alt_ingest_state_path"]


def alt_ingest_state_path() -> str:
    return os.getenv("RAG_TG_ALT_INGEST_STATE_FILE", "/var/lib/rugbrain/tg_alt_ingest_state.json")


class AltIngestState:
    """``{channel_id: last_msg_id}`` with a crash-safe atomic save."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or alt_ingest_state_path()
        self._channels: dict[str, int] = {}

    @property
    def channels(self) -> dict[str, int]:
        return dict(self._channels)

    def load(self) -> AltIngestState:
        try:
            with open(self._path) as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return self
        if not isinstance(raw, dict):
            return self
        channels = raw.get("channels")
        if isinstance(channels, dict):
            self._channels = {str(k): int(v) for k, v in channels.items() if _as_int(v) is not None}
        return self

    def last_msg_id(self, channel_id: int) -> int:
        return int(self._channels.get(str(channel_id), 0))

    def record(self, channel_id: int, msg_id: int) -> None:
        if msg_id > self.last_msg_id(channel_id):
            self._channels[str(channel_id)] = int(msg_id)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = f"{self._path}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"channels": self._channels}, fh)
        os.replace(tmp, self._path)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
