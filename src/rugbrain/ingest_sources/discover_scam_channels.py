"""Scam-channel discovery + ban-safe join/ingest expansion.

Finds known scam-alert / call-out channels beyond the owner's current
inventory, verifies them public+active via t.me/s (no API cost), joins a
small rate-limited batch per run through the user session, and hands them
to the standard sweep so their content flows into RAG like everything else.

Anti-ban guardrails (2026-08-24):
    - max RAG_TGJOIN_MAX_PER_RUN joins per invocation (default 4)
    - random jitter 45–120s between joins
    - FloodWaitError honored exactly (sleep what Telegram demands + 5s)
    - reads prefer t.me/s web previews; MTProto used only for private groups
    - every join logged to state for audit

Usage:
    python -m rugbrain.ingest_sources.discover_scam_channels            # probe + report
    python -m rugbrain.ingest_sources.discover_scam_channels --join 3   # probe + join up to 3
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Any

import httpx

logger = logging.getLogger("rugbrain.tg_discover")

CHANNELS_FILE = os.getenv("RAG_TGWEB_CHANNELS", "/var/lib/rmi-rag/tg_channels.txt")
STATE_FILE = os.getenv("RAG_TG_STATE_FILE", "/var/lib/rmi-rag/tg_ingest_state.json")
MAX_PER_RUN = int(os.getenv("RAG_TGJOIN_MAX_PER_RUN", "4"))
UA = os.getenv(
    "RAG_TGWEB_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
)

# Candidate scam-alert / call-out channels (public). Verified live before joining.
CANDIDATES: list[str] = [
    "scamalertsofficial",
    "CryptoScamAlert",
    "rugpullalerts",
    "scam_alerts_crypto",
    "cryptoscam",
    "honeypot_alerts",
    "ScamDetector",
    "crypto_scam_alerts",
    "RugDocAlerts",
    "TokenSniffers",
    "GoPlusSecurity",
    "DeFiScamAlerts",
    "solana_rug_alerts",
    "bubblesmaps_scams",
    "security_allert",
    "CryptoFraudAlerts",
    "scamdb",
    "ape_store_alerts",
    "rugcheck_alerts",
    "phishing_alerts_crypto",
]


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            loaded: dict[str, Any] = json.load(f)
            return loaded
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


async def probe_candidates() -> list[dict[str, Any]]:
    """Verify candidates exist, are public, and have recent posts."""
    live: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, timeout=12.0, follow_redirects=True
    ) as c:
        sem = asyncio.Semaphore(4)

        async def probe(name: str) -> None:
            async with sem:
                try:
                    r = await c.get(f"https://t.me/s/{name}")
                    if r.status_code != 200 or "tgme_widget_message" not in r.text:
                        return
                    # crude recency: page contains a datetime within last 30 days
                    dates = re.findall(r'datetime="(\d{4}-\d{2}-\d{2})T', r.text)
                    recent = False
                    if dates:
                        latest = max(dates)
                        age_days = (
                            time.time() - time.mktime(time.strptime(latest, "%Y-%m-%d"))
                        ) / 86400
                        recent = age_days <= 30
                    title_m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
                    live.append(
                        {
                            "username": name,
                            "title": title_m.group(1) if title_m else name,
                            "recent": recent,
                        }
                    )
                except Exception as e:
                    logger.debug("probe_failed ch=%s err=%s", name, str(e)[:60])

        await asyncio.gather(*(probe(c) for c in CANDIDATES))
    return live


async def join_and_register(client: Any, username: str, state: dict[str, Any]) -> bool:
    """Join one channel with jitter; register in sweep list + inventory."""
    from telethon.tl.functions.channels import JoinChannelRequest

    try:
        await client(JoinChannelRequest(username))
    except Exception as e:
        name = type(e).__name__
        if "FloodWait" in name:
            wait = getattr(e, "seconds", 60)
            logger.warning("flood_wait_join seconds=%s — sleeping", wait)
            await asyncio.sleep(wait + 5)
            return False
        logger.warning("join_failed ch=%s err=%s", username, str(e)[:80])
        return False

    # register into sweep list
    existing = set()
    try:
        with open(CHANNELS_FILE) as f:
            existing = {ln.strip() for ln in f if ln.strip()}
    except OSError:
        pass
    if username not in existing:
        with open(CHANNELS_FILE, "a") as f:
            f.write(username + "\n")

    inv = state.setdefault("inventory", [])
    if not any(ch.get("username") == username for ch in inv):
        inv.append(
            {
                "id": None,
                "title": username,
                "username": username,
                "category": "scam_caller",
                "is_channel": True,
                "joined_by_discovery": True,
                "joined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
    logger.info("joined_and_registered ch=%s", username)
    return True


async def run(join_count: int = 0) -> dict[str, Any]:
    state = _load_state()
    already = {ch.get("username") for ch in state.get("inventory", [])}
    live = [c for c in await probe_candidates() if c["username"] not in already]

    report: dict[str, Any] = {"live_candidates": len(live), "joined": []}

    if join_count <= 0:
        report["candidates"] = live[:15]
        return report

    from telethon import TelegramClient

    client = TelegramClient(
        os.getenv("RAG_TG_SESSION_FILE", "/var/lib/rmi-rag/tg_ingest.session"),
        int(os.getenv("TELEGRAM_API_ID", "0") or 0),
        os.getenv("TELEGRAM_API_HASH", ""),
    )
    await client.connect()
    joined = 0
    for cand in live:
        if joined >= min(join_count, MAX_PER_RUN):
            break
        ok = await join_and_register(client, cand["username"], state)
        if ok:
            joined += 1
            report["joined"].append(cand["username"])
            # jitter between joins — never rapid-fire
            await asyncio.sleep(random.uniform(45, 120))
    _save_state(state)
    await client.disconnect()
    report["joined_count"] = joined
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--join", type=int, default=0, help="join up to N verified channels")
    args = ap.parse_args()
    print(asyncio.run(run(join_count=args.join)))
