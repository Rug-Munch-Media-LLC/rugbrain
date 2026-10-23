"""Discover scam/shill/promo channels the account has ALREADY joined.

The userbot's dialog list is the cheapest inventory we own: 219 dialogs, no
API cost, no joins, no ban risk. ``discover_scam_channels`` covers the other
direction (guessing handles from the open web and joining them, one rate-
limited batch per run); this module never joins anything, so it is safe to run
while the sweepers are live.

Two sources, and the default is the safe one:

* ``inventory`` (default) — the dialog snapshot the userbot already wrote to
  ``tg_ingest_state.json``. No Telethon connection, therefore no chance of two
  processes sharing one auth key.
* ``live`` (opt-in, ``--live``) — one short ``iter_dialogs`` read. It is the
  path that has burned us: ``AuthKeyDuplicatedError`` means the same session
  file was used from two IPs at once, and the key is then dead forever.

Public reachability is verified accountlessly over ``t.me/s/<handle>`` — same
method the web ingester uses — so a handle is only registered when the
accountless sweeper can actually read it.

Usage:
    python -m rugbrain.ingest_sources.discover_joined_channels            # report only
    python -m rugbrain.ingest_sources.discover_joined_channels --write    # append new handles
    python -m rugbrain.ingest_sources.discover_joined_channels --live     # read the session
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from rugbrain.ingest_sources.channel_registry import append_channels
from rugbrain.ingest_sources.joined_classifier import classify_joined

logger = logging.getLogger("rugbrain.tg_discover_joined")

INVENTORY_FILE = os.getenv("RAG_TG_INVENTORY", "/var/lib/rugbrain/tg_ingest_state.json")
CHANNELS_FILE = os.getenv("RAG_TGWEB_CHANNELS", "/var/lib/rugbrain/tg_channels.txt")
UA = os.getenv(
    "RAG_TGWEB_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
)


class DuplicateSessionError(RuntimeError):
    """The auth key is unusable — do NOT retry, do NOT open a second reader."""


@dataclass(frozen=True)
class Joined:
    """One joined dialog, reduced to what classification needs."""

    title: str
    username: str
    category: str | None


def dialogs_from_inventory(path: str = INVENTORY_FILE) -> list[Joined]:
    """Read the userbot's dialog snapshot; no network, no session, no lock."""
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("inventory_unreadable path=%s err=%s", path, exc)
        return []
    dialogs: list[Joined] = []
    for row in state.get("inventory", []):
        username = (row.get("username") or "").strip().lstrip("@")
        if not username:
            continue  # private group: the accountless sweeper cannot read it
        title = row.get("title") or ""
        dialogs.append(
            Joined(title=title, username=username, category=classify_joined(title, username))
        )
    return dialogs


async def dialogs_from_session() -> list[Joined]:
    """One short bounded read of the live dialog list. Never retried."""
    from telethon.errors import AuthKeyDuplicatedError

    from rugbrain.ingest_sources.tg_client import build_client

    client = build_client()
    dialogs: list[Joined] = []
    try:
        await client.connect()
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            username = (getattr(entity, "username", "") or "").strip()
            if not username or not getattr(entity, "broadcast", False):
                continue
            title = getattr(entity, "title", "") or ""
            dialogs.append(Joined(title, username, classify_joined(title, username)))
    except AuthKeyDuplicatedError as exc:
        raise DuplicateSessionError(
            "session auth key was used from two IPs at once and is now dead. "
            "Do not retry and do not start another reader: re-authorise a fresh "
            "session, and keep one process per account."
        ) from exc
    finally:
        await client.disconnect()
    return dialogs


async def probe_public(handles: list[str]) -> list[str]:
    """Return the handles whose ``t.me/s`` preview is live right now."""
    live: list[str] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, timeout=12.0, follow_redirects=True
    ) as c:
        sem = asyncio.Semaphore(6)

        async def one(handle: str) -> None:
            async with sem:
                try:
                    r = await c.get(f"https://t.me/s/{handle}")
                except Exception as exc:
                    logger.debug("probe_failed ch=%s err=%s", handle, str(exc)[:60])
                    return
                if r.status_code == 200 and "tgme_widget_message" in r.text:
                    live.append(handle)

        await asyncio.gather(*(one(h) for h in handles))
    return live


async def run(*, write: bool = False, live: bool = False) -> dict[str, Any]:
    """Classify joined channels, verify them publicly, optionally register them."""
    dialogs = await dialogs_from_session() if live else dialogs_from_inventory()
    candidates = [d for d in dialogs if d.category]
    reachable = await probe_public(sorted({d.username for d in candidates}))
    reachable_set = {h.lower() for h in reachable}
    added = append_channels(reachable, CHANNELS_FILE) if write else []
    report: dict[str, Any] = {
        "source": "live_session" if live else "inventory",
        "dialogs": len(dialogs),
        "classified": len(candidates),
        "by_category": {},
        "public_reachable": len(reachable),
        "added": added,
    }
    for d in candidates:
        key = d.category or "none"
        report["by_category"][key] = report["by_category"].get(key, 0) + 1
    report["unreachable"] = sorted(
        {d.username for d in candidates if d.username.lower() not in reachable_set}
    )
    return report


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="discover scam/shill channels already joined")
    ap.add_argument(
        "--write", action="store_true", help="append reachable handles to the channel list"
    )
    ap.add_argument(
        "--live", action="store_true", help="read the live dialog list (one shot, no retry)"
    )
    args = ap.parse_args()
    try:
        print(json.dumps(asyncio.run(run(write=args.write, live=args.live)), indent=2))
    except DuplicateSessionError as exc:
        raise SystemExit(f"BLOCKED: {exc}") from exc
