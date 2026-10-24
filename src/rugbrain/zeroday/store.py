"""File-backed store: cluster state, record snapshot, change feed, latency.

Small on purpose — one directory, atomic writes, no database. The MCP surface
and the CLI read the same files, so there is a single source of truth.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_FILE = "state.json"
RECORDS_FILE = "records.jsonl"
FEED_FILE = "feed.jsonl"
LATENCY_FILE = "latency.json"


def default_root() -> Path:
    """``ZERODAY_STORE`` env override, else ``<repo>/data/zeroday``."""
    env = os.getenv("ZERODAY_STORE", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "zeroday"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Store:
    """One directory holding the pipeline's durable outputs."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def path(self, name: str) -> Path:
        return self.root / name

    def load_state(self) -> dict[str, Any]:
        """Cluster state, or an empty state when the file is missing/corrupt."""
        try:
            data = json.loads(self.path(STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, state: dict[str, Any]) -> None:
        _atomic_write(self.path(STATE_FILE), json.dumps(state, indent=1, sort_keys=True))

    def write_snapshot(self, records: list[dict[str, Any]]) -> None:
        lines = "\n".join(json.dumps(r, sort_keys=True) for r in records)
        _atomic_write(self.path(RECORDS_FILE), lines + ("\n" if lines else ""))

    def append_feed(self, changes: list[dict[str, Any]]) -> None:
        if not changes:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path(FEED_FILE).open("a", encoding="utf-8") as handle:
            for change in changes:
                handle.write(json.dumps(change, sort_keys=True) + "\n")

    def write_latency(self, latency: dict[str, Any]) -> None:
        _atomic_write(self.path(LATENCY_FILE), json.dumps(latency, indent=1, sort_keys=True))

    def read_records(self) -> list[dict[str, Any]]:
        """Snapshot rows; malformed lines are skipped, never fabricated."""
        try:
            text = self.path(RECORDS_FILE).read_text(encoding="utf-8")
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def read_feed(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = self.path(FEED_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def summary(self) -> dict[str, Any]:
        records = self.read_records()
        return {
            "root": str(self.root),
            "records": len(records),
            "kinds": _count(records, "kind"),
            "sources": _count(records, "source"),
            "feed": len(self.read_feed(limit=10_000)),
        }


def _count(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "?")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["FEED_FILE", "LATENCY_FILE", "RECORDS_FILE", "STATE_FILE", "Store", "default_root"]
