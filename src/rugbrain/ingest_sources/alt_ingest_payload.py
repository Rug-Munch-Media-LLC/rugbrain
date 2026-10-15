"""Payload normalization + identity for alt-ingest Telegram messages.

Pure functions only (no I/O), so they are unit-testable without a Telethon
client or a live Qdrant.

Two independent dedup identities, both required:

1. **(channel_id, message_id)** — the message's own identity. Never re-fetched
   (state file) and never re-written (a ``has_point`` existence gate on the
   deterministic point id).
2. **content hash** — the sha256 of the normalized text, taken from the
   existing helper in ``telegram_userbot`` (NOT reimplemented here). This is
   what collapses a story cross-posted by N channels into one stored document:
   it is the only gate that catches a re-post with a *different* message id.

The payload shape mirrors ``news_v2`` (via ``rugbrain.ingest_common``), so an
ingested message lands in ``news_v2_8b`` and is served by the same query paths
as the bot's own news sweep.

IMPORTANT: This is an ALT-ACCOUNT INGEST (read-only consumer). The MAIN account
(@cryptorugmunch bot) does the posting — this module must NEVER post.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from rugbrain.ingest_common import CrawlDocument

# Reuse the repo's existing content-hash helper instead of writing a second.
from rugbrain.ingest_sources.telegram_userbot import content_hash

__all__ = [
    "COLLECTION",
    "MIN_MESSAGE_CHARS",
    "dedup_key",
    "message_is_new",
    "message_to_doc",
    "point_id_for_doc",
    "stable_point_id",
]

#: Collection the alt-ingest writes to. The store resolves this to
#: ``social_intel_8b`` (declared twin). Operator directive 2026-10-15: the X +
#: Telegram social scrape is social-origin content and belongs in social_intel,
#: not in news_v2 — published media (RSS/news sites/newsletters) owns news_v2.
COLLECTION = "social_intel"

#: Below this a "message" is media-only / sticker noise (telegram_userbot rule).
MIN_MESSAGE_CHARS = 30


def stable_point_id(channel_id: int, msg_id: int) -> str:
    """Deterministic identity seed for one ``(channel_id, message_id)`` pair.

    The store converts a doc_id to UUIDv5, so this is the *ingredient* of the
    point id, not a second id scheme. Two runs over the same channel therefore
    land the same point and overwrite rather than duplicate.
    """
    return f"telegram:{channel_id}:{msg_id}"


def point_id_for_doc(channel_id: int, msg_id: int, chunk_hash: str) -> str:
    """The doc_id actually handed to the store, stable per channel/message/chunk."""
    return f"{stable_point_id(channel_id, msg_id)}:{chunk_hash}"


def message_to_doc(
    *, channel: str, channel_id: int, channel_title: str, msg: Any
) -> CrawlDocument | None:
    """Normalize one Telethon message into a ``news_v2`` CrawlDocument.

    Returns ``None`` for media-only / sticker noise so the caller counts a real
    skip rather than ingesting an empty document.
    """
    text = (msg.message or "").strip()
    if len(text) < MIN_MESSAGE_CHARS:
        return None
    ts = ""
    if getattr(msg, "date", None) is not None:
        date = msg.date
        ts = date.astimezone(UTC).isoformat() if hasattr(date, "astimezone") else str(date)
    source = f"t.me/{channel}"
    return CrawlDocument(
        title=f"{channel_title or channel}: {text[:80]}",
        content=f"[TG/alt-ingest] {channel_title or channel}: {text[:4000]}",
        url=f"https://t.me/{channel}/{msg.id}",
        source=source,
        collection=COLLECTION,
        category="crypto_news",
        published=ts,
        severity="low",
        chain="unknown",
        metadata={
            "kind": "telegram_message",
            "channel": channel,
            "channel_id": channel_id,
            "msg_id": msg.id,
            "content_hash": content_hash(text),
            "ts": ts,
        },
    )


def dedup_key(channel_id: int, msg_id: int) -> str:
    """The state-file key that remembers the newest message id per channel."""
    return f"{channel_id}:{msg_id}"


def message_is_new(channel_id: int, msg_id: int, last_seen: dict[str, int]) -> bool:
    """True when *msg_id* is newer than the last message seen for its channel."""
    return msg_id > int(last_seen.get(str(channel_id), 0))


def newest_msg_id(msg_ids: list[int], fallback: int) -> int:
    """The highest message id seen in this run for one channel."""
    return max(msg_ids, default=fallback)


__all__ = [
    "COLLECTION",
    "MIN_MESSAGE_CHARS",
    "content_hash",
    "dedup_key",
    "message_is_new",
    "message_to_doc",
    "newest_msg_id",
    "point_id_for_doc",
    "stable_point_id",
]
