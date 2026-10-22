"""Per-feed health + HTTP validator state for the RSS ingest.

Two things every RSS poller needs and this fleet was missing:

* **Conditional GET.** We store the ``ETag`` / ``Last-Modified`` a feed returns
  and replay it as ``If-None-Match`` / ``If-Modified-Since``. A 304 means no
  bytes to parse and no rows to consider — the cheapest possible poll.
* **Per-feed last_error / last_success.** A feed that 403s for a week must be
  visible as a row, not as a missing log line. ``rss_feed_status`` is that row,
  and read paths can rank sources by freshness without re-probing.

State lives in ``data/rss_feed_validators.json`` (atomic replace) and in the
Postgres ``rss_feed_status`` table (created on first use).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALIDATORS_PATH = Path(
    os.getenv(
        "RMI_RSS_VALIDATORS",
        str(Path(__file__).resolve().parents[2] / "data/rss_feed_validators.json"),
    )
)

DDL = """
CREATE TABLE IF NOT EXISTS rss_feed_status (
    source          TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    status          TEXT NOT NULL,
    http_status     INT,
    items           INT NOT NULL DEFAULT 0,
    new_items       INT NOT NULL DEFAULT 0,
    not_modified    BOOLEAN NOT NULL DEFAULT FALSE,
    last_success_at TIMESTAMPTZ,
    last_error      TEXT,
    last_error_at   TIMESTAMPTZ,
    etag            TEXT,
    last_modified   TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rss_status_status ON rss_feed_status(status);
"""

UPSERT = """
INSERT INTO rss_feed_status (source, url, status, http_status, items, new_items,
                             not_modified, last_success_at, last_error, last_error_at,
                             etag, last_modified, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW())
ON CONFLICT (source) DO UPDATE SET
    url = EXCLUDED.url,
    status = EXCLUDED.status,
    http_status = EXCLUDED.http_status,
    items = EXCLUDED.items,
    new_items = EXCLUDED.new_items,
    not_modified = EXCLUDED.not_modified,
    last_success_at = COALESCE(EXCLUDED.last_success_at, rss_feed_status.last_success_at),
    last_error = EXCLUDED.last_error,
    last_error_at = COALESCE(EXCLUDED.last_error_at, rss_feed_status.last_error_at),
    etag = COALESCE(EXCLUDED.etag, rss_feed_status.etag),
    last_modified = COALESCE(EXCLUDED.last_modified, rss_feed_status.last_modified),
    updated_at = NOW()
"""


def load_validators() -> dict[str, dict[str, str]]:
    """Read the validator cache; a corrupt file degrades to an empty cache."""
    try:
        return json.loads(VALIDATORS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_validators(state: dict[str, dict[str, str]]) -> None:
    """Atomic write so a killed run never leaves a half-written cache."""
    VALIDATORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=VALIDATORS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, VALIDATORS_PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def conditional_headers(state: dict[str, dict[str, str]], url: str) -> dict[str, str]:
    """Replay stored validators for ``url`` (empty dict when unknown)."""
    entry = state.get(url) or {}
    headers: dict[str, str] = {}
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    return headers


def remember_validators(
    state: dict[str, dict[str, str]], url: str, etag: str | None, last_modified: str | None
) -> None:
    """Keep the newest validators for ``url``; unchanged feeds keep the old pair."""
    if not etag and not last_modified:
        return
    current = state.get(url) or {}
    state[url] = {
        "etag": etag or current.get("etag", ""),
        "last_modified": last_modified or current.get("last_modified", ""),
    }


async def ensure_status_table(conn: Any) -> None:
    await conn.execute(DDL)


async def record_status(
    conn: Any,
    *,
    source: str,
    url: str,
    status: str,
    http_status: int | None = None,
    items: int = 0,
    new_items: int = 0,
    not_modified: bool = False,
    error: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    """Upsert one feed's health row (never raises: a status write must not stop ingest)."""
    now = datetime.now(UTC)
    ok = status == "ok" or not_modified
    with contextlib.suppress(Exception):  # health telemetry must not stop ingest
        await conn.execute(
            UPSERT,
            source,
            url,
            status,
            http_status,
            items,
            new_items,
            not_modified,
            now if ok else None,
            error[:500] if error else None,
            None if ok else now,
            etag,
            last_modified,
        )
