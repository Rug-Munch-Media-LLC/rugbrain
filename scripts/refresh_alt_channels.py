"""Refresh the alt-ingest channel footprint: pinned ∪ the account's dialogs.

Also ATTEMPTS to join pinned channels the account is missing (public ones),
recording flood-waits instead of dying. Data only lands in
data/alt_ingest_extra_channels.txt — the pinned list in
alt_ingest_channels.py is never edited by this script.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.ingest_sources.alt_ingest_channels import ALT_INGEST_CHANNELS
from rugbrain.ingest_sources.alt_ingest_client import build_alt_ingest_client

OUT = Path(__file__).resolve().parents[1] / "data" / "alt_ingest_extra_channels.txt"


async def main() -> None:
    client = build_alt_ingest_client()
    await client.start()
    dialogs: dict[str, str] = {}
    async for d in client.iter_dialogs():
        if d.is_channel and getattr(d.entity, "username", None):
            dialogs[d.entity.username.lower()] = str(d.entity.title or "")
    pinned = [c.lower().lstrip("@") for c in ALT_INGEST_CHANNELS]
    missing = [c for c in pinned if c not in dialogs]
    extras = sorted(set(dialogs) - set(pinned))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "# alt-account footprint beyond the bot's pinned list (generated)\n"
        + "\n".join(extras) + "\n",
        encoding="utf-8",
    )
    print(f"pinned={len(pinned)} joined={len(dialogs)} extras_written={len(extras)} missing={len(missing)}")
    joined, blocked = 0, []
    for handle in missing:
        try:
            await client(aspect_type="channel") if False else None
            from telethon.tl.functions.channels import JoinChannelRequest

            await client(JoinChannelRequest(handle))
            joined += 1
        except Exception as exc:  # noqa: BLE001 — flood-wait/private: report, continue
            blocked.append(f"{handle}:{str(exc)[:60]}")
    print(f"join_attempted={len(missing)} join_ok={joined} join_blocked={len(blocked)}")
    for b in blocked[:10]:
        print("BLOCKED", b)
    await client.disconnect()


asyncio.run(main())
