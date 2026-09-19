"""The ``SecurityAlert`` model — one detected crypto security incident."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class SecurityAlert(BaseModel):
    """A single detected crypto security incident.

    ``id`` is a deterministic UUIDv5 derived from ``source + title + date`` so
    re-detection of the same incident maps to the same Qdrant point (upsert is
    idempotent). ``detected_at`` records when *we* first saw it.
    """

    source: str
    title: str
    description: str = ""
    url: str = ""
    severity: str = "high"
    attack_vector: str = "unknown"
    chain: str = "unknown"
    protocol: str = ""
    amount_lost_usd: float = 0.0
    date: str = ""
    detected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        seed = f"alert:{self.source}:{self.title.strip().lower()}:{self.date}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

    def text(self) -> str:
        """Render the incident as embeddable text."""
        parts = [
            f"Security alert: {self.title}",
            f"Source: {self.source}",
            f"Date: {self.date or 'unknown'}",
            f"Protocol: {self.protocol or 'unknown'}",
            f"Chain: {self.chain or 'unknown'}",
            f"Attack vector: {self.attack_vector or 'unknown'}",
            f"Amount lost: ${self.amount_lost_usd:,.0f}"
            if self.amount_lost_usd
            else "Amount lost: unknown",
        ]
        if self.description:
            parts.append(f"Details: {self.description}")
        if self.url:
            parts.append(f"URL: {self.url}")
        return "\n".join(parts)
