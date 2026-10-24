"""Environment knobs for the Docling document-conversion lane.

The lane is deliberately opt-in: ``DOCLING_ENABLED`` on *and* ``DOCLING_URL``
set, otherwise every document keeps the local markitdown/pypdf path and the
ingestion behaviour is unchanged. Consuming an external docling-serve rather
than embedding the models keeps the RugBrain image small.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_TIMEOUT = 120.0
TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class DoclingSettings:
    """Resolved docling-serve configuration for one conversion."""

    url: str = ""
    enabled: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES
    timeout: float = DEFAULT_TIMEOUT
    table_mode: str = "accurate"

    @property
    def usable(self) -> bool:
        """True only when the lane is enabled and points at a service."""
        return bool(self.enabled and self.url)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return max(int(raw), 1) if raw else default
    except ValueError:
        log.warning("docling_env_invalid name=%s value=%r", name, raw)
        return default


def settings_from_env() -> DoclingSettings:
    """Read ``DOCLING_URL``, ``DOCLING_ENABLED``, ``DOCLING_MAX_BYTES``."""
    return DoclingSettings(
        url=os.getenv("DOCLING_URL", "").strip().rstrip("/"),
        enabled=os.getenv("DOCLING_ENABLED", "").strip().lower() in TRUTHY,
        max_bytes=_int_env("DOCLING_MAX_BYTES", DEFAULT_MAX_BYTES),
        table_mode=os.getenv("DOCLING_TABLE_MODE", "accurate").strip() or "accurate",
    )


__all__ = ["DEFAULT_MAX_BYTES", "DoclingSettings", "settings_from_env"]
