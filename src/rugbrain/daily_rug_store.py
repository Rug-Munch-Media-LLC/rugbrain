"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Durable story store for the Daily Rug long-form.

Publication is an upsert keyed by slug: the same topic on the same day
overwrites the record and bumps ``revision`` instead of minting a second URL.
The store is the DI seam a Ghost or HTTP publisher plugs into; this module
knows only ``upsert`` and ``get`` over plain dicts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

STORY_DIR_ENV = "RUGBRAIN_STORY_DIR"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


class StoryStore(Protocol):
    """Anything that can persist one story per slug."""

    def upsert(self, story: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, slug: str) -> dict[str, Any] | None: ...


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class FileStoryStore:
    """One JSON file per slug, replaced atomically so readers never see a stub."""

    root: Path

    def path_for(self, slug: str) -> Path:
        if not SLUG_RE.match(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        return self.root / f"{slug}.json"

    def get(self, slug: str) -> dict[str, Any] | None:
        path = self.path_for(slug)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def upsert(self, story: dict[str, Any]) -> dict[str, Any]:
        slug = str(story["slug"])
        path = self.path_for(slug)
        previous = self.get(slug) or {}
        record = dict(story)
        record["revision"] = int(previous.get("revision", 0)) + 1
        record["created_at"] = previous.get("created_at") or _now()
        record["updated_at"] = _now()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return record


def default_store() -> FileStoryStore:
    """``RUGBRAIN_STORY_DIR`` or ``<repo>/data/stories``."""
    override = os.getenv(STORY_DIR_ENV, "").strip()
    root = Path(override) if override else Path(__file__).resolve().parents[2] / "data" / "stories"
    return FileStoryStore(root)
