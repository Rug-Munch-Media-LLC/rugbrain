"""Load the provenance catalogue from data, not code.

The catalogue is JSON so an operator can add a publisher, record its licence and
re-run the verifier without touching Python. This module owns exactly one job:
read it, validate it, return specs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rugbrain.ingest_sources.content_sources.spec import SourceSpec, parse_specs

__all__ = ["DEFAULT_PATH", "load_catalog"]

#: Repo-root catalogue. Overridable with RUGBRAIN_CONTENT_SOURCES for tests.
DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "content_sources.json"


def load_catalog(
    path: str | Path | None = None, *, include_inactive: bool = False
) -> tuple[SourceSpec, ...]:
    """Return the validated catalogue, enabled sources by default.

    ``include_inactive`` adds rows that were verified but have nothing to serve
    right now (dormant channels, egress-blocked publishers) — reported, never
    silently fetched.
    """
    resolved = Path(path or os.getenv("RUGBRAIN_CONTENT_SOURCES") or DEFAULT_PATH)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    rows = list(payload.get("sources") or [])
    if include_inactive:
        rows += list(payload.get("inactive") or [])
    return parse_specs(rows)
