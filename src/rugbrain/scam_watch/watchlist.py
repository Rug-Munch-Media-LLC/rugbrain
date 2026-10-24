"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Watchlist loading. The seed file is public data only: channel name, handle and
URL. Subscriber counts are left ``null`` until a dated, sourced read exists —
an unsourced number is worse than no number in an investigations file.
"""

from __future__ import annotations

import json
from pathlib import Path

from rugbrain.scam_watch.models import ChannelRecord

#: Repo-root config path (src/rugbrain/scam_watch/watchlist.py -> repo root).
DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parents[3] / "config" / "youtube_watchlist.json"

#: Schema version understood by this loader.
WATCHLIST_VERSION = 1


def load_watchlist(path: Path | str | None = None) -> tuple[ChannelRecord, ...]:
    """Load and validate the watchlist, rejecting duplicate handles.

    A duplicate handle would double-count a channel in every report, so it is a
    hard error rather than a silent dedupe.
    """
    target = Path(path) if path is not None else DEFAULT_WATCHLIST_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("version") != WATCHLIST_VERSION:
        raise ValueError(f"watchlist version must be {WATCHLIST_VERSION}")
    records = tuple(ChannelRecord.model_validate(row) for row in payload["channels"])
    handles = [record.handle.lower() for record in records]
    if len(set(handles)) != len(handles):
        raise ValueError("watchlist contains duplicate handles")
    return records
