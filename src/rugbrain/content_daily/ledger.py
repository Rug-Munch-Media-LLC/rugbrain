"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The publication ledger: what went out, and therefore what must never go out twice.

Two guarantees come from one append-only file:

*   **skip-if-already-published** — a run key that already has a ``published``
    row is skipped, so a persistent systemd timer that fires after a reboot, a
    retried unit, or an operator running the CLI by hand cannot post the same
    edition twice.
*   **cross-run dedupe** — the ``story_refs`` of the last two weeks stop the
    same story anchoring two editions on the same day.

The file is append-only and single-writer (one edition per slot, all slots
serialised by the unit). Lines are written with one ``write`` of a short
string, which is atomic enough under ``O_APPEND`` for a one-writer journal;
this is deliberately not a database.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER_ENV = "RUGBRAIN_CONTENT_LEDGER"
REF_WINDOW_DAYS = 14


def _default_path() -> Path:
    override = os.getenv(LEDGER_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "content" / "daily-ledger.jsonl"


class DailyLedger:
    """Append-only record of published editions and delivered messages."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _latest(self, kind: str, run_key: str) -> dict[str, Any] | None:
        found = [
            row for row in self._rows() if row.get("kind") == kind and row.get("run_key") == run_key
        ]
        return found[-1] if found else None

    def published(self, run_key: str) -> dict[str, Any] | None:
        """The publication row for ``run_key``, or None when it has not run."""
        return self._latest("published", run_key)

    def mark_published(
        self,
        run_key: str,
        *,
        edition: str,
        day: str,
        slug: str,
        url: str,
        title: str,
        story_refs: list[str],
    ) -> None:
        """Record a successful site publication."""
        self._append(
            {
                "kind": "published",
                "run_key": run_key,
                "edition": edition,
                "day": day,
                "slug": slug,
                "url": url,
                "title": title,
                "story_refs": story_refs,
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    def delivered(self, run_key: str) -> dict[str, Any] | None:
        """The delivery row for ``run_key``, or None when Telegram was not sent."""
        return self._latest("delivered", run_key)

    def mark_delivered(
        self, run_key: str, *, channel: str, message_ids: list[int | str]
    ) -> None:
        """Record the Telegram message ids so a later run can prove the send."""
        self._append(
            {
                "kind": "delivered",
                "run_key": run_key,
                "channel": channel,
                "message_ids": list(message_ids),
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    def recent_refs(self, *, days: int = REF_WINDOW_DAYS, today: str | None = None) -> set[str]:
        """Story refs published within ``days``, for cross-run dedupe."""
        floor = (
            datetime.strptime(today, "%Y-%m-%d").date() if today else datetime.now(UTC).date()
        ) - timedelta(days=days)
        refs: set[str] = set()
        for row in self._rows():
            if row.get("kind") != "published":
                continue
            try:
                day = datetime.strptime(str(row.get("day")), "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= floor:
                refs.update(str(ref) for ref in row.get("story_refs") or [])
        return refs


__all__ = ["DailyLedger", "LEDGER_ENV", "REF_WINDOW_DAYS"]
