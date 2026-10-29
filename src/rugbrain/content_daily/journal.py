"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Per-stage observability: one JSONL row per stage, one per run, one health line.

The journal answers three questions without reading code: how long each stage
took, how many items went in and came out (and what dedupe dropped), and which
edition last succeeded. It never raises on write — a content run that published
correctly must not be marked failed because a log directory was full.

Location: ``RUGBRAIN_CONTENT_LOG``, else ``/var/log/rugbrain/content-daily.jsonl``
when that directory exists (the fleet's existing log location, same place as
``embed-lane-failures.jsonl``), else ``<repo>/data/content/daily-journal.jsonl``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_ENV = "RUGBRAIN_CONTENT_LOG"
FLEET_LOG_DIR = Path("/var/log/rugbrain")
FLEET_LOG_NAME = "content-daily.jsonl"


def _default_path() -> Path:
    override = os.getenv(LOG_ENV, "").strip()
    if override:
        return Path(override)
    if FLEET_LOG_DIR.is_dir() and os.access(FLEET_LOG_DIR, os.W_OK):
        return FLEET_LOG_DIR / FLEET_LOG_NAME
    return Path(__file__).resolve().parents[3] / "data" / "content" / "daily-journal.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    """Append-only JSONL journal with a last-run-per-edition health view."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()

    def write(self, row: dict[str, Any]) -> bool:
        """Append one row. Returns False (never raises) when the write fails."""
        record = {"ts": _now(), **row}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"content-daily: journal write failed: {exc}", file=sys.stderr)
            return False
        return True

    def stage(
        self,
        run_key: str,
        *,
        edition: str,
        day: str,
        stage: str,
        ok: bool,
        ms: float,
        items_in: int | None = None,
        items_out: int | None = None,
        attempts: int = 1,
        error_class: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record one stage outcome."""
        self.write(
            {
                "kind": "stage",
                "run_key": run_key,
                "edition": edition,
                "day": day,
                "stage": stage,
                "ok": ok,
                "ms": round(ms, 1),
                "items_in": items_in,
                "items_out": items_out,
                "attempts": attempts,
                "error_class": error_class,
                "detail": detail or {},
            }
        )

    def run(self, record: dict[str, Any]) -> None:
        """Record the run envelope (status, url, message ids, drops, errors)."""
        self.write({"kind": "run", **record})

    def rows(self) -> list[dict[str, Any]]:
        """Every row, oldest first; unreadable lines are skipped, not fatal."""
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def health(self) -> dict[str, dict[str, Any]]:
        """Last run per edition: ``{edition: {day, status, ts, url, error}}``."""
        latest: dict[str, dict[str, Any]] = {}
        for row in self.rows():
            if row.get("kind") != "run":
                continue
            latest[str(row.get("edition"))] = {
                "day": row.get("day"),
                "status": row.get("status"),
                "ts": row.get("ts"),
                "url": row.get("url"),
                "error": (row.get("errors") or [None])[0],
            }
        return latest

    def health_line(self, editions: tuple[str, ...] = ("morning", "midday", "evening")) -> str:
        """One-line operator view: ``morning=ok@… midday=failed@… evening=never``."""
        latest = self.health()
        parts: list[str] = []
        for key in editions:
            row = latest.get(key)
            if not row:
                parts.append(f"{key}=never")
                continue
            state = row["status"] or "unknown"
            note = f"@{row['ts']}" if row.get("ts") else ""
            if row.get("url"):
                note += f" {row['url']}"
            elif row.get("error"):
                note += f" {row['error']}"
            parts.append(f"{key}={state}{note}")
        return "content-daily health: " + " | ".join(parts)


__all__ = ["FLEET_LOG_DIR", "Journal", "LOG_ENV"]
