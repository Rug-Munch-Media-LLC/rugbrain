"""The critical-alert event model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class CriticalAlert(BaseModel):
    """A single detected exploit/critical threat.

    ``id`` is a deterministic UUIDv5 derived from a stable event key.  The
    timestamp is deliberately excluded: the same transaction is re-polled and
    must map to the same store point across polls.
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
            if self.tx_hash:
                event_key = f"tx:{self.tx_hash}"
            elif self.url:
                event_key = f"url:{self.url}"
            else:
                pair = self.raw.get("pair")
                if pair:
                    tokens = self.raw.get("tokens", "")
                    event_key = f"pair:{self.chain}:{pair}:{tokens}"
                else:
                    # Titles are the last-resort stable key for feed-only
                    # alerts; detected_at is never part of the identity.
                    event_key = f"title:{self.title}"
            self.id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.source}:{event_key}"))

    def text(self) -> str:
        parts = [self.title, self.description]
        if self.amount_usd:
            parts.append(f"${self.amount_usd:,.0f} lost")
        return " | ".join(p for p in parts if p)
