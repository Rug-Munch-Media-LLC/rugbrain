"""One immutable source description, plus the provenance every item carries.

A source is ingestible only when it names who publishes it, under what licence
basis, and when the feed was last verified. Those facts travel with every stored
chunk into Qdrant metadata, so a retrieval result can always be traced back to a
publisher and a terms basis — the requirement that keeps summary-only
syndication defensible.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

__all__ = ["KINDS", "STATUSES", "SourceSpec", "parse_specs"]

KINDS = frozenset({"news", "newsletter", "medium", "paragraph", "youtube"})
STATUSES = frozenset({"enabled", "inactive", "blocked"})
_REQUIRED = ("id", "kind", "publisher", "feed_url", "licence", "fetched_at")


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """A publisher we fetch, with the provenance needed to store its content."""

    id: str
    kind: str
    publisher: str
    feed_url: str
    licence: str
    fetched_at: str
    homepage: str = ""
    licence_note: str = ""
    category: str = "news"
    language: str = "en"
    channel_id: str = ""
    status: str = "enabled"
    verified_http_status: int = 0

    def provenance(self) -> dict[str, Any]:
        """The traceable facts attached to every item from this source."""
        return {
            "source_id": self.id,
            "kind": self.kind,
            "publisher": self.publisher,
            "source_url": self.homepage or self.feed_url,
            "feed_url": self.feed_url,
            "licence": self.licence,
            "licence_note": self.licence_note,
            "fetched_at": self.fetched_at,
        }


_FIELD_NAMES = frozenset(f.name for f in fields(SourceSpec))


def _text(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _check_enums(ident: str, row: dict[str, Any]) -> tuple[str, str]:
    """Validate kind and status; return them (both are structural, not free text)."""
    kind = _text(row, "kind")
    if kind not in KINDS:
        raise ValueError(f"content source {ident!r} has unknown kind {kind!r}")
    status = _text(row, "status") or "enabled"
    if status not in STATUSES:
        raise ValueError(f"content source {ident!r} has unknown status {status!r}")
    return kind, status


def _check_url(ident: str, row: dict[str, Any]) -> str:
    feed_url = _text(row, "feed_url")
    if not feed_url.startswith("https://"):
        raise ValueError(f"content source {ident!r} must fetch over https")
    return feed_url


def _spec_from_row(ident: str, row: dict[str, Any]) -> SourceSpec:
    kind, status = _check_enums(ident, row)
    return SourceSpec(
        id=ident,
        kind=kind,
        publisher=_text(row, "publisher"),
        feed_url=_check_url(ident, row),
        licence=_text(row, "licence"),
        fetched_at=_text(row, "fetched_at"),
        homepage=_text(row, "homepage"),
        licence_note=_text(row, "licence_note"),
        category=_text(row, "category") or "news",
        language=_text(row, "language") or "en",
        channel_id=_text(row, "channel_id"),
        status=status,
        verified_http_status=int(row.get("verified_http_status") or 0),
    )


def parse_specs(rows: list[dict[str, Any]]) -> tuple[SourceSpec, ...]:
    """Validate raw catalogue rows into specs. Raises on an incomplete row.

    Incomplete provenance is a hard error, not a default: a source without a
    licence or a verified-at stamp cannot be cited, so it cannot be ingested.
    """
    seen: set[str] = set()
    specs: list[SourceSpec] = []
    for row in rows:
        ident = _text(row, "id")
        missing = [name for name in _REQUIRED if not _text(row, name)]
        if missing:
            raise ValueError(f"content source {ident or '<no id>'!r} missing {missing}")
        if ident in seen:
            raise ValueError(f"duplicate content source id {ident!r}")
        seen.add(ident)
        specs.append(_spec_from_row(ident, row))
    return tuple(specs)
