"""Single source of truth for RAG chunking settings (``chunking_config.yaml``).

Every ingestion path resolves its sizes here instead of hard-coding them, so the
YAML is enforced rather than decorative. A missing, unreadable or invalid config
raises :class:`ChunkingConfigError` at import (loud startup failure), and a
collection with no entry is also a hard error — silent fallbacks are what let the
dead config drift from the code in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).with_name("chunking_config.yaml")


class ChunkingConfigError(RuntimeError):
    """Chunking config is missing, unreadable, invalid, or lacks a collection."""


@dataclass(frozen=True)
class ChunkSettings:
    """Validated chunking parameters for one content collection."""

    strategy: str
    chunk_size: int
    overlap: float
    max_chars: int


def load_chunking_config(path: Path | str | None = None) -> dict[str, ChunkSettings]:
    """Parse + validate the chunking YAML; raise loudly on anything unusable."""
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    try:
        raw: Any = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ChunkingConfigError(f"chunking config unreadable: {cfg_path}: {exc}") from exc
    entries = (raw or {}).get("rag_chunking") if isinstance(raw, dict) else None
    if not isinstance(entries, dict) or not entries:
        raise ChunkingConfigError(f"{cfg_path}: missing or empty 'rag_chunking' mapping")
    if "default" not in entries:
        raise ChunkingConfigError(f"{cfg_path}: no 'default' entry (needed for unknown collections)")
    out: dict[str, ChunkSettings] = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            raise ChunkingConfigError(f"{cfg_path}: entry {name!r} is not a mapping")
        size, overlap, cap = entry.get("chunk_size"), entry.get("overlap"), entry.get("max_chars")
        if not isinstance(size, int) or size <= 0:
            raise ChunkingConfigError(f"{cfg_path}: {name}.chunk_size must be a positive int")
        if not isinstance(overlap, (int, float)) or not 0 <= float(overlap) <= 1:
            raise ChunkingConfigError(f"{cfg_path}: {name}.overlap must be a ratio in [0, 1]")
        if not isinstance(cap, int) or cap < size:
            raise ChunkingConfigError(f"{cfg_path}: {name}.max_chars must be an int >= chunk_size")
        out[name] = ChunkSettings(str(entry.get("strategy", "recursive")), size, float(overlap), cap)
    return out


CONFIG: dict[str, ChunkSettings] = load_chunking_config()


def chunk_settings(collection: str | None) -> ChunkSettings:
    """Settings for *collection*; unknown collections are a hard error."""
    key = (collection or "default").strip() or "default"
    try:
        return CONFIG[key]
    except KeyError:
        raise ChunkingConfigError(
            f"collection {key!r} has no chunking entry; add it to {CONFIG_PATH.name}"
        ) from None
