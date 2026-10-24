"""The mirror sweep: pull new messages, dedupe, embed on 8B, upsert into news_v2_8b.

The primary function of this module is :func:`run_mirror` — one sweep of the
channels @cryptorugmunch's bot follows, served by the SECOND Telethon account,
with bounded batches and two independent dedup gates before any embed call.

The embed/store stage and the credential resolution are separate modules
(``telegram_mirror_store`` / ``telegram_mirror_client``) so this one stays a
single orchestration function.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rugbrain.ingest_sources.alt_dialogs import dialog_entities
from rugbrain.ingest_sources.alt_ingest_channels import resolve_channel_list
from rugbrain.ingest_sources.alt_ingest_client import (
    AltIngestCredentialError,
    alt_ingest_credentials_present,
    build_alt_ingest_client,
)
from rugbrain.ingest_sources.alt_ingest_payload import message_to_doc
from rugbrain.ingest_sources.alt_ingest_state import AltIngestState
from rugbrain.ingest_sources.alt_ingest_store import embed_and_store

logger = logging.getLogger("rugbrain.tg_mirror")

__all__ = [
    "LIMIT_PER_CHANNEL",
    "AltIngestCredentialError",
    "alt_ingest_credentials_present",
    "collect_payloads",
    "fetch_channel",
    "run_mirror",
]

#: Sane default bound per channel (matches the bot's TG_NEWS_PER_CHANNEL_LIMIT).
LIMIT_PER_CHANNEL = 50


def _summary(total_channels: int) -> dict[str, Any]:
    return {
        "channels": total_channels,
        "channels_fetched": 0,
        "channels_failed": 0,
        "messages_seen": 0,
        "messages_skipped_noise": 0,
        "candidates": 0,
        "duplicate_hash": 0,
        "duplicate_msgid": 0,
        "inserted": 0,
        "failed": 0,
        "errors": [],
        "_seen_hashes": set(),
    }


async def fetch_channel(
    client: Any,
    handle: str,
    state: AltIngestState,
    limit: int,
    summary: dict[str, Any],
    entity: Any = None,
) -> list[Any]:
    """Resolve one channel, pull its new messages, normalize + dedupe them.

    A single channel failing (deleted handle, private, flood-wait) must never
    abort the sweep — the error is recorded and the run continues.

    Both dedup gates are applied here so the embed stage receives content that
    is NEW under both identities:
      * ``(channel_id, message_id)`` — via the state file (``min_id`` fetch);
      * content hash — via the in-run ``seen`` set, which collapses a story
        cross-posted by N channels into ONE embed.
    """
    docs: list[Any] = []
    seen: set[str] = summary["_seen_hashes"]
    try:
        if entity is None:
            # Only resolves usernames the dialogs map did NOT already cover —
            # every avoided call is a ResolveUsername rate-limit hit dodged.
            entity = await client.get_entity(handle)
        chan_id = int(getattr(entity, "id", 0) or 0)
        title = str(getattr(entity, "title", "") or handle)
        newest = state.last_msg_id(chan_id)
        async for msg in client.iter_messages(entity, limit=limit, min_id=newest):
            summary["messages_seen"] += 1
            newest = max(newest, int(msg.id))
            doc = message_to_doc(channel=handle, channel_id=chan_id, channel_title=title, msg=msg)
            if doc is None:
                summary["messages_skipped_noise"] += 1
                continue
            if doc.metadata.get("content_hash") in seen:
                summary["duplicate_hash"] += 1
                continue
            seen.add(doc.metadata["content_hash"])
            docs.append(doc)
    except Exception as exc:
        summary["channels_failed"] += 1
        summary["errors"].append(f"{handle}: {str(exc)[:120]}")
        logger.warning("mirror_channel_failed ch=%s err=%s", handle, str(exc)[:120])
        return []
    state.record(chan_id, newest)
    summary["channels_fetched"] += 1
    return docs





async def collect_payloads(
    client: Any, handles: list[str], state: AltIngestState, limit: int
) -> dict[str, Any]:
    """Fetch every handle, apply both dedup gates, return docs + summary."""
    summary = _summary(len(handles))
    docs: list[Any] = []
    entities = await dialog_entities(client)
    summary["dialog_channels"] = len(entities)
    for handle in handles:
        key = handle.lower().lstrip("@")
        docs.extend(
            await fetch_channel(client, handle, state, limit, summary, entity=entities.get(key))
        )
    summary.pop("_seen_hashes", None)
    summary["candidates"] = len(docs)
    return {"docs": docs, "summary": summary}


__all__.extend(["collect_payloads", "dialog_entities", "fetch_channel"])


async def run_mirror(
    channels: list[str] | None,
    *,
    limit_per_channel: int = LIMIT_PER_CHANNEL,
    dry_run: bool = False,
    client_factory: Callable[[], Any] | None = None,
    embedder_factory: Callable[[], Any] | None = None,
    store_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """One mirror sweep. Returns a summary dict with exact dedup counts.

    ``dry_run`` fetches + dedupes but never embeds. ``client_factory`` is the
    seam tests use; in production it resolves to the second account's client.
    """
    ok, missing = alt_ingest_credentials_present()
    if not ok:
        raise AltIngestCredentialError(f"missing credentials: {', '.join(missing)}")

    handles = resolve_channel_list(channels)
    state = AltIngestState().load()
    client = (client_factory or build_alt_ingest_client)()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AltIngestCredentialError(
                "second-account session not authorized — bootstrap it with "
                "python -m rugbrain.ingest_sources.alt_ingest_channels --login"
            )
        result = await collect_payloads(client, handles, state, limit_per_channel)
    finally:
        await client.disconnect()

    summary = result["summary"]
    docs = result["docs"]
    if docs:
        # The CLI's --verify probe needs a sample that was actually ingested.
        summary["verify_sample"] = docs[0].content
    if not dry_run and docs:
        await embed_and_store(
            docs, summary, embedder_factory=embedder_factory, store_factory=store_factory
        )
    state.save()
    logger.info(
        "mirror_done channels=%d/%d messages=%d candidates=%d dup_hash=%d "
        "dup_msgid=%d inserted=%d failed=%d",
        summary["channels_fetched"],
        summary["channels"],
        summary["messages_seen"],
        summary["candidates"],
        summary["duplicate_hash"],
        summary["duplicate_msgid"],
        summary["inserted"],
        summary["failed"],
    )
    return summary
