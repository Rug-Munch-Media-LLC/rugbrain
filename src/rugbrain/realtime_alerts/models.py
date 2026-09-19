"""The critical-alert event model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class CriticalAlert(BaseModel):
    """A single detected exploit/critical threat.

    ``id`` is a deterministic UUIDv5 derived from ``source + title +
    detected_at`` so re-detection of the same incident maps to the same
    store point (upserts are idempotent).
    """

    id: str = ""
    title: str
    severity: str = "critical"
    chain: str = "unknown"
    source: str = "unknown"
    description: str = ""
    amount_usd: float = 0.0
    addresses: list[str] = Field(default_factory=list)
    tx_hash: str = ""
    url: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    detected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "active"

    @classmethod
    def from_candidate(cls, **kwargs: Any) -> CriticalAlert:
        """Build an alert, filling classification fields from free text."""
        from rugbrain import realtime_alerts as _pkg

        text = " ".join(str(v) for v in (kwargs.get("title", ""), kwargs.get("description", "")))
        kwargs.setdefault("severity", "critical")
        kwargs.setdefault("chain", _pkg.detect_chain_critical(text))
        kwargs.setdefault("amount_usd", _pkg.parse_amount_usd(text))
        return cls(**kwargs)

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            basis = f"{self.source}:{self.title}:{self.detected_at}"
            self.id = str(uuid.uuid5(uuid.NAMESPACE_URL, basis))

    def text(self) -> str:
        parts = [self.title, self.description]
        if self.amount_usd:
            parts.append(f"${self.amount_usd:,.0f} lost")
        return " | ".join(p for p in parts if p)
