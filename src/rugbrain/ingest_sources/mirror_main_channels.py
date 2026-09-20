"""Mirror the main account's channel membership onto the RM Insight scraper account.

Reads the main account's persisted inventory (public usernames) and joins them
on the SCRAPER account using ITS OWN session — never shared with the main
ingester. Private chats (no username) are reported as needing a manual invite.

Ban-safety / anti-detection:
    - random batch size (default 3-6 joins per run, not a fixed 4)
    - shuffled join order (no predictable alphabetical sweep)
    - wide jitter 45-180s + occasional deep "idle" pause between joins
    - FloodWaitError honored exactly (sleep + 5s)
    - skips channels already joined (tracked in scraper state)
    - plus: sticky Webshare proxy + distinct device fingerprint (airgap)

Env (scraper account — SEPARATE from the main ingester env):
    TELEGRAM_API_ID / TELEGRAM_API_HASH   rminsight app (39738133)
    RAG_TG_SESSION_FILE                   default /var/lib/rugbrain/insight/tg_insight.session
    RAG_TG_STATE_FILE                     default /var/lib/rugbrain/insight/tg_insight_state.json
    TELEGRAM_PHONE                        only needed for --login
    MAIN_INVENTORY_FILE                   default /var/lib/rugbrain/insight/main_inventory.json

Usage:
    python -m rugbrain.ingest_sources.mirror_main_channels --report     # accounting
    python -m rugbrain.ingest_sources.mirror_main_channels --login      # bootstrap scraper session
    python -m rugbrain.ingest_sources.mirror_main_channels --join       # join a random batch (3-6)
    python -m rugbrain.ingest_sources.mirror_main_channels --join 4     # join up to 4 public channels
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

logger = logging.getLogger("rugbrain.tg_mirror")

MAIN_INVENTORY_FILE = os.getenv(
    "MAIN_INVENTORY_FILE", "/var/lib/rugbrain/insight/main_inventory.json"
)
SESSION_FILE = os.getenv("RAG_TG_SESSION_FILE", "/var/lib/rugbrain/insight/tg_insight.session")
STATE_FILE = os.getenv("RAG_TG_STATE_FILE", "/var/lib/rugbrain/insight/tg_insight_state.json")
MAX_PER_RUN = int(os.getenv("RAG_TGJOIN_MAX_PER_RUN", "6"))
MIN_PER_RUN = int(os.getenv("RAG_TGJOIN_MIN_PER_RUN", "3"))
MIN_DELAY = int(os.getenv("RAG_TGJOIN_MIN_DELAY", "45"))
MAX_DELAY = int(os.getenv("RAG_TGJOIN_MAX_DELAY", "180"))
DEEP_PAUSE_PROB = float(os.getenv("RAG_TGJOIN_DEEP_PAUSE_PROB", "0.15"))
PHONE = os.getenv("TELEGRAM_PHONE", "")


def _load_main() -> dict[str, Any]:
    with open(MAIN_INVENTORY_FILE) as f:
        return json.load(f)  # type: ignore[no-any-return]


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return {"joined": {}, "last_run": None}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _make_client() -> Any:
    from telethon import TelegramClient

    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH not set (source rmi-insight-tg.env)"
        )

    kwargs: dict[str, Any] = {}

    # Airgap #1: egress via a sticky Webshare HTTP proxy — distinct IP from the
    # main account (which connects direct from Talos). Breaks IP correlation.
    proxy_host = os.getenv("TELEGRAM_PROXY_HOST", "")
    proxy_port = os.getenv("TELEGRAM_PROXY_PORT", "")
    if proxy_host and proxy_port:
        kwargs["proxy"] = (
            "http",
            proxy_host,
            int(proxy_port),
            True,  # rdns
            os.getenv("TELEGRAM_PROXY_USER", ""),
            os.getenv("TELEGRAM_PROXY_PASS", ""),
        )

    # Airgap #2: distinct device fingerprint — Telegram sees a different
    # "device" than the main account's default (system.machine / kernel / telethon).
    if os.getenv("TELEGRAM_DEVICE_MODEL"):
        kwargs["device_model"] = os.getenv("TELEGRAM_DEVICE_MODEL")
    if os.getenv("TELEGRAM_SYSTEM_VERSION"):
        kwargs["system_version"] = os.getenv("TELEGRAM_SYSTEM_VERSION")
    if os.getenv("TELEGRAM_APP_VERSION"):
        kwargs["app_version"] = os.getenv("TELEGRAM_APP_VERSION")
    if os.getenv("TELEGRAM_LANG_CODE"):
        kwargs["lang_code"] = os.getenv("TELEGRAM_LANG_CODE")
        kwargs["system_lang_code"] = os.getenv("TELEGRAM_LANG_CODE")

    return TelegramClient(SESSION_FILE, api_id, api_hash, **kwargs)


async def _login() -> None:
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.start(phone=PHONE)  # prompts for code + 2FA
    me = await client.get_me()
    print(f"Authorized as: {me.first_name} (@{me.username}) id={me.id}")
    await client.disconnect()


async def _report() -> None:
    main = _load_main()
    state = _load_state()
    pub = main.get("public_usernames", [])
    already = set(state.get("joined", {}))
    remaining = [u for u in pub if u not in already]
    print(f"main account total:            {main.get('total', 0)}")
    print(f"  public (joinable):          {len(pub)}")
    print(f"  private (need invite):      {main.get('private_need_invite', 0)}")
    print(f"scraper account:              {len(already)} joined so far")
    print(f"  remaining to join:          {len(remaining)}")


async def _join_one(client: Any, username: str) -> str:
    """Join a single public channel/group by username. Returns 'joined'|'flood'|'error'."""
    from telethon.tl.functions.channels import JoinChannelRequest

    try:
        await client(JoinChannelRequest(username))
        return "joined"
    except Exception as e:
        name = type(e).__name__
        if "FloodWait" in name:
            wait = getattr(e, "seconds", 60)
            logger.warning("flood_wait_join seconds=%s", wait)
            await asyncio.sleep(wait + 5)
            return "flood"
        if "Already" in name or "USER_ALREADY_PARTICIPANT" in str(e):
            return "joined"
        logger.warning("join_failed ch=%s err=%s", username, str(e)[:80])
        return "error"


async def _run_join(join_count: int) -> None:
    main = _load_main()
    state = _load_state()
    joined = state.setdefault("joined", {})
    pub = main.get("public_usernames", [])
    todo = [u for u in pub if u not in joined]

    rng = random.SystemRandom()  # crypto-grade entropy for unpredictable pacing
    rng.shuffle(todo)  # random join order — avoid a predictable alphabetical sweep

    if join_count <= 0:  # random batch size per run
        join_count = rng.randint(MIN_PER_RUN, MAX_PER_RUN)

    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("scraper session not authorized — run --login first")

    done = 0
    for username in todo:
        if done >= join_count:
            break
        logger.info("joining %s (%d/%d)", username, done + 1, join_count)
        result = await _join_one(client, username)
        if result == "joined":
            joined[username] = {"joined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            done += 1
            if done < join_count:
                # human-ish pacing: wide jitter + occasional deep "idle" gap
                await asyncio.sleep(rng.uniform(MIN_DELAY, MAX_DELAY))
                if rng.random() < DEEP_PAUSE_PROB:
                    gap = rng.uniform(120, 300)
                    logger.info("idle_pause seconds=%d", int(gap))
                    await asyncio.sleep(gap)
        elif result == "flood":
            # FloodWait already slept; stop this run to stay safe
            break
    _save_state(state)
    await client.disconnect()
    remaining = [u for u in pub if u not in joined]
    print(f"joined this run: {done}")
    print(f"total joined:    {len(joined)}/{len(pub)}")
    print(f"remaining:       {len(remaining)}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Mirror main account channels onto scraper account")
    ap.add_argument("--login", action="store_true", help="one-time interactive session bootstrap")
    ap.add_argument("--report", action="store_true", help="print join accounting")
    ap.add_argument(
        "--join",
        type=int,
        nargs="?",
        const=-1,
        default=None,
        help="join public channels (omit the count for a random 3-6 batch)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.login:
        asyncio.run(_login())
    elif args.report:
        asyncio.run(_report())
    elif args.join is not None:
        asyncio.run(_run_join(args.join))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
