#!/usr/bin/env python3
"""Which collections the DAILY permanence contract covers.

A backup contract that no collection can ever satisfy is not a contract, it is
a permanent false alarm plus a day that never completes. This module makes the
scope explicit and machine-readable.

MEASURED BASIS (2026-10-23, journal + B2 manifest):
  rag_documents_8b   1,136,363 points @ 24.9 KB/point = 28.3 GB
                     1,243 MB per 50k shard, 5.1-9.7 min per shard (median 8.3)
                     23 shards => ~190 min of export+upload
  nightly budget     4 firings x 30 min = 120 min for ALL collections
  => the collection cannot fit inside the day it belongs to. Pinning
  expected_shards makes it *countable*, but no schedule makes it *fit*.

So it moves to a dedicated weekly contract (config/permanence-scope.json and
rugbrain-permanence-elephant.timer), and the daily denominator drops by one.
The manifest records the exclusion in `daily_scope` so a reader (and the
monitor's denominator) can tell "97 of 97 by design" from "97 of 98, one
missing".

One primary function: `daily_scope`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("permanence_scope")

SCOPE_FILE = Path(__file__).resolve().parent.parent / "config" / "permanence-scope.json"


def daily_scope(collections: list[str]) -> tuple[list[str], list[str]]:
    """Split `collections` into (daily work, excluded-by-contract).

    A missing or unreadable scope file excludes NOTHING: a broken config must
    never silently shrink what gets backed up. Failing open here means the
    nightly tries harder, not that data is dropped.
    """
    try:
        payload = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
        excluded = [str(n) for n in (payload.get("daily_excluded") or [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("scope_file_unreadable path=%s err=%s", SCOPE_FILE, exc)
        return list(collections), []

    known = set(collections)
    present = [n for n in excluded if n in known]
    if present:
        logger.warning(
            "daily_scope_excluded collections=%s reason=weekly_contract",
            ",".join(present),
        )
    # The DECLARED list is returned, not only the names present in this call:
    # `upload_manifest` calls this with an empty list purely to stamp the
    # contract onto the manifest, and an empty answer there would read as
    # "nothing is excluded".
    return [c for c in collections if c not in set(excluded)], list(excluded)
