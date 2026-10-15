"""Telegram user-account ingestion — every channel/group the owner's account is in.

Part of the social-intelligence layer (2026-08-24 directive): scrape ALL TG
channels the cryptorugmunch account is a member of, categorize them, dedupe
cross-posted stories, rerank by importance, detect coordinated shill
campaigns, and feed everything into the RAG corpus so the news/alerts/scan
channels and downstream agents work from the full firehose.

Why a USER session (MTProto) and not the bot API:
    Bots only see channels they admin. The owner's account is in hundreds of
    channels including private scam-caller/shiller groups — enumerating those
    requires Telethon with an authorized user session.

Session policy (IMPORTANT):
    This ingester uses ITS OWN session — never share a session file or
    StringSession with another running process (concurrent use of one auth
    key gets sessions killed by Telegram). Bootstrap once:

        python -m rugbrain.ingest_sources.telegram_userbot --login

    then let the scheduler run it periodically:

        python -m rugbrain.ingest_sources.telegram_userbot --cycle

Config (env):
    TELEGRAM_API_ID / TELEGRAM_API_HASH   from gopass [INTERNAL_SECRET_PATH]
    TELETHON_SESSION_STRING               exported StringSession (preferred)
    RAG_TG_STATE_FILE                     default /var/lib/rugbrain/tg_ingest_state.json
    RAG_TG_MAX_MESSAGES_PER_CHANNEL       default 40 per cycle
    RAG_TG_MAX_CHANNELS_PER_CYCLE         default 150 per cycle (BUDGET, not a
                                          filter — see sweep_order.py: channels
                                          rotate by staleness so none starves)
    RAG_TG_SHILL_WINDOW_MINUTES           default 90
    RAG_TG_SHILL_MIN_CHANNELS             default 4

Collections (through the standard store; interim suffix applies automatically):
    news_v2          categorized news/data chatter (default)
    threat_intel_v2  scam-callouts, exploit warnings, shill campaigns (high severity)

Dedup layers:
    1. (channel_id, msg_id)      — never reprocess a message
    2. sha256(normalized text)   — identical text across channels = one story
    3. shill grouping            — same ticker/CA echoed across N channels in
                                   the window becomes ONE campaign record
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Classification (channel_taxonomy) and sweep ordering (sweep_order) live in
# their own modules — one purpose each, both unit-testable without a session.
# Re-exported here: CATEGORY_RULES/CATEGORY_WEIGHT/categorize were part of this
# module's surface and stay part of it.
from rugbrain.ingest_sources import ingest_cycle, tg_client
from rugbrain.ingest_sources.channel_taxonomy import (
    CATEGORY_RULES,  # noqa: F401 — re-exported: part of this module's surface
    CATEGORY_WEIGHT,
    categorize,
)
from rugbrain.ingest_sources.sweep_order import mark_swept, order_for_sweep, track_channels

logger = logging.getLogger("rugbrain.tg_userbot")

# ── Config ────────────────────────────────────────────────────────────────

# Session file and credentials are resolved by tg_client at call time, so a
# second account only needs its own env file. (The old module-level constants
# also misspelled the session-string env var as TELETHON_SESSION_STRING, so
# RAG_TG_SESSION_STRING could never have worked.)
STATE_FILE = os.getenv("RAG_TG_STATE_FILE", "/var/lib/rugbrain/tg_ingest_state.json")
MAX_PER_CHANNEL = int(os.getenv("RAG_TG_MAX_MESSAGES_PER_CHANNEL", "40"))
SHILL_WINDOW_MIN = int(os.getenv("RAG_TG_SHILL_WINDOW_MINUTES", "90"))
SHILL_MIN_CHANNELS = int(os.getenv("RAG_TG_SHILL_MIN_CHANNELS", "4"))
MAX_CHANNELS_PER_CYCLE = int(os.getenv("RAG_TG_MAX_CHANNELS_PER_CYCLE", "150"))

TICKER_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,15}\b")
CA_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")

IMPORTANCE_KEYWORDS: dict[str, float] = {
    "hack": 3.0,
    "exploit": 3.0,
    "drained": 3.0,
    "stolen": 2.5,
    "rug": 2.5,
    "rugpull": 3.0,
    "honeypot": 2.5,
    "scam": 2.0,
    "liquidity": 1.5,
    "audit": 1.0,
    "warning": 1.5,
    "alert": 1.5,
    "arrest": 2.5,
    "sec": 1.5,
    "lawsuit": 1.5,
    "ban": 1.0,
    "launch": 0.5,
    "listing": 0.5,
    "airdrop": 0.5,
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()[:16]


def importance_score(text: str, category: str, echo_count: int) -> float:
    t = normalize_text(text)
    kw = sum(w for k, w in IMPORTANCE_KEYWORDS.items() if k in t)
    base = min(kw, 6.0) * CATEGORY_WEIGHT.get(category, 0.7)
    # Cross-channel echo amplifies (same story posted widely = more important)
    return round(base * (1.0 + 0.25 * max(0, echo_count - 1)), 2)


@dataclass
class TgMessage:
    channel_id: int
    channel_title: str
    category: str
    msg_id: int
    text: str
    date: datetime
    c_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.c_hash = content_hash(self.text)


@dataclass
class ShillCampaign:
    token: str
    channels: list[str]
    message_count: int
    first_seen: datetime
    sample_texts: list[str]

    def severity(self) -> str:
        return "critical" if len(self.channels) >= SHILL_MIN_CHANNELS * 2 else "high"


# ── State ─────────────────────────────────────────────────────────────────


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)  # type: ignore[no-any-return]  # json.load returns Any
    except (OSError, json.JSONDecodeError):
        return {"channels": {}, "seen_hashes": [], "last_cycle": None}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    # Cap the seen-hash ring (keep most recent 20k)
    hashes = state.get("seen_hashes", [])
    state["seen_hashes"] = hashes[-20000:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


# ── Ingestion cycle ───────────────────────────────────────────────────────


async def _get_client() -> Any:
    # Client construction (including proxy egress) lives in tg_client so this
    # module does not grow and so a second account can reuse the same cycle.
    return tg_client.build_client()


async def _enumerate_channels(client: Any) -> list[dict[str, Any]]:
    """Enumerate every channel/group the account participates in."""
    inventory: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel and not dialog.is_group:
            continue
        ent = dialog.entity
        username = getattr(ent, "username", "") or ""
        title = getattr(ent, "title", "") or dialog.name or ""
        inventory.append(
            {
                "id": dialog.id,
                "title": title,
                "username": username,
                "category": categorize(title, username),
                "is_channel": bool(dialog.is_channel),
                "members": getattr(ent, "participants_count", None),
            }
        )
    return inventory


async def _fetch_new_messages(
    client: Any, chan: dict[str, Any], state: dict[str, Any]
) -> list[TgMessage]:
    last_id = int(state["channels"].get(str(chan["id"]), {}).get("last_msg_id", 0))
    msgs: list[TgMessage] = []
    try:
        async for m in client.iter_messages(chan["id"], limit=MAX_PER_CHANNEL, min_id=last_id):
            text = (m.message or "").strip()
            if not text or len(text) < 30:  # skip media-only/sticker noise
                continue
            msgs.append(
                TgMessage(
                    channel_id=chan["id"],
                    channel_title=chan["title"],
                    category=chan["category"],
                    msg_id=m.id,
                    text=text,
                    date=m.date.astimezone(UTC) if m.date else datetime.now(UTC),
                )
            )
    except Exception as e:
        logger.warning("tg_fetch_failed channel=%s err=%s", chan["title"][:30], str(e)[:80])
    return msgs


def _detect_shill_campaigns(msgs: list[TgMessage]) -> list[ShillCampaign]:
    """Same ticker/contract echoed across many channels inside the window."""
    cutoff = datetime.now(UTC) - timedelta(minutes=SHILL_WINDOW_MIN)
    recent = [m for m in msgs if m.date >= cutoff]
    by_token: dict[str, set[str]] = defaultdict(set)
    samples: dict[str, list[TgMessage]] = defaultdict(list)

    for m in recent:
        tokens = set(TICKER_RE.findall(m.text)) | {c[:10] + "…" for c in CA_RE.findall(m.text)}
        for tok in tokens:
            tok_l = tok.lower()
            by_token[tok_l].add(m.channel_title)
            if len(samples[tok_l]) < 3:
                samples[tok_l].append(m)

    campaigns: list[ShillCampaign] = []
    for tok, chans in by_token.items():
        if len(chans) >= SHILL_MIN_CHANNELS:
            group = samples[tok]
            campaigns.append(
                ShillCampaign(
                    token=tok.upper(),
                    channels=sorted(chans),
                    message_count=len(chans),
                    first_seen=min(m.date for m in group),
                    sample_texts=[m.text[:300] for m in group],
                )
            )
    campaigns.sort(key=lambda c: -len(c.channels))
    return campaigns


def _docs_from_messages(msgs: list[TgMessage], echo: dict[str, int]) -> list[dict[str, Any]]:
    """Dedupe by content hash; one doc per unique story with metadata."""
    seen_hashes: set[str] = set()
    docs: list[dict[str, Any]] = []
    for m in sorted(
        msgs, key=lambda x: -importance_score(x.text, x.category, echo.get(x.c_hash, 1))
    ):
        if m.c_hash in seen_hashes:
            continue
        seen_hashes.add(m.c_hash)
        imp = importance_score(m.text, m.category, echo.get(m.c_hash, 1))
        sev = (
            "critical" if imp >= 6 else "high" if imp >= 3.5 else "medium" if imp >= 1.5 else "low"
        )
        docs.append(
            {
                "content": f"[TG/{m.category}] {m.channel_title}: {m.text[:4000]}",
                # Social-origin chatter -> social_intel (operator 2026-10-15);
                # only critical/high severity escalates to threat_intel.
                "collection": "threat_intel_v2" if sev in ("critical", "high") else "social_intel",
                "severity": sev,
                "source": f"telegram:{m.channel_title[:40]}",
                "metadata": {
                    "kind": "telegram_message",
                    "category": m.category,
                    "importance": imp,
                    "echo_count": echo.get(m.c_hash, 1),
                    "msg_id": m.msg_id,
                    "channel_id": m.channel_id,
                    "ts": m.date.isoformat(),
                },
            }
        )
    return docs


def _campaign_docs(campaigns: list[ShillCampaign]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for c in campaigns:
        sample = "\n---\n".join(c.sample_texts)[:4000]
        docs.append(
            {
                "content": (
                    f"[SHILL CAMPAIGN] ${c.token} pushed across {len(c.channels)} channels "
                    f"in {SHILL_WINDOW_MIN}min: {', '.join(c.channels[:12])}. Samples:\n{sample}"
                ),
                "collection": "threat_intel_v2",
                "severity": c.severity(),
                "source": "telegram:shill_detector",
                "metadata": {
                    "kind": "shill_campaign",
                    "token": c.token,
                    "channel_count": len(c.channels),
                    "channels": c.channels[:25],
                    "ts": c.first_seen.isoformat(),
                },
            }
        )
    return docs


async def run_cycle(max_channels: int = MAX_CHANNELS_PER_CYCLE) -> dict[str, Any]:
    """One ingestion sweep: enumerate → fetch new → dedupe → rerank → ingest."""
    started = time.time()
    state = _load_state()
    client = await _get_client()
    summary: dict[str, Any] = {
        "channels_seen": 0,
        "messages": 0,
        "unique_docs": 0,
        "campaigns": 0,
        "ingested": 0,
        "errors": [],
    }
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram session not authorized. Run: "
                "python -m rugbrain.ingest_sources.telegram_userbot --login"
            )

        inventory = await _enumerate_channels(client)
        summary["channels_seen"] = len(inventory)
        state["inventory"] = inventory  # full categorized inventory persists

        # Every enumerated channel is tracked, so state converges on the
        # inventory instead of trailing it by the quiet channels.
        summary["channels_tracked_added"] = track_channels(state, inventory)

        # Staleness order: never-swept first, then oldest last_swept. The cap
        # is a per-cycle BUDGET — with a rotating cursor no channel starves.
        ordered = order_for_sweep(inventory, state, max_channels)
        summary["channels_swept"] = len(ordered)
        logger.info(
            "tg_sweep_budget inventory=%d budget=%d swept=%d tracked=%d",
            len(inventory),
            max_channels,
            len(ordered),
            len(state.get("channels") or {}),
        )

        all_msgs: list[TgMessage] = []
        for ch in ordered:
            msgs = await _fetch_new_messages(client, ch, state)
            all_msgs.extend(msgs)
            # Stamp EVERY swept channel, including those with 0 messages,
            # otherwise a quiet channel is re-fetched first on every cycle.
            mark_swept(state, ch["id"], max((m.msg_id for m in msgs), default=0))
        summary["messages"] = len(all_msgs)

        # Intelligence stage (2026-09-28): gate -> cluster -> score -> credit.
        # Before this the seen-hash ring was the whole dedupe story and it was
        # saturated, so older items were re-admitted every cycle while five
        # syndications of one story were stored as separate documents.
        kept, state["channel_credit"], cycle_stats = ingest_cycle.process_cycle(
            all_msgs,
            seen_hashes=state.get("seen_hashes", []),
            credit=state.get("channel_credit") or {},
        )
        # Echo counts: how many messages share a content hash (cross-channel
        # corroboration, used by the importance score and campaign detection).
        echo = ingest_cycle.echo_counts(all_msgs)
        state["seen_hashes"] = cycle_stats.pop("seen_hashes")
        summary.update(cycle_stats)
        if cycle_stats.get("channels_to_retire"):
            logger.info(
                "tg_credit_floor count=%d sample=%s",
                len(cycle_stats["channels_to_retire"]),
                cycle_stats["channels_to_retire"][:5],
            )

        campaigns = _detect_shill_campaigns(kept)
        docs = _docs_from_messages(kept, echo) + _campaign_docs(campaigns)
        summary["unique_docs"] = len(docs)
        summary["campaigns"] = len(campaigns)

        if docs:
            from rugbrain.embeddings import get_embedding
            from rugbrain.ingest_common import CrawlDocument, store_documents
            from rugbrain.qdrant_store import get_qdrant_store

            crawl_docs = [
                CrawlDocument(
                    title=d["content"][:120],
                    content=d["content"],
                    url="",
                    source=d["source"],
                    collection=d["collection"],
                    category=d["metadata"].get("category", "telegram"),
                    published=d["metadata"].get("ts", ""),
                    severity=d["severity"],
                    metadata=d["metadata"],
                )
                for d in docs
            ]

            class _Embedder:
                async def embed_one(self, text: str) -> list[float]:
                    return await get_embedding(text)

            res = await store_documents(crawl_docs, _Embedder(), await get_qdrant_store())
            summary["ingested"] = res.get("inserted", 0)
            summary["failed"] = res.get("failed", 0)

        state["last_cycle"] = datetime.now(UTC).isoformat()
        _save_state(state)
        summary["seconds"] = round(time.time() - started, 1)
        logger.info(
            "tg_cycle_done channels=%d msgs=%d unique=%d campaigns=%d ingested=%d "
            "gated_out=%d clusters=%d collapsed=%d",
            summary["channels_seen"],
            summary["messages"],
            summary["unique_docs"],
            summary["campaigns"],
            summary["ingested"],
            summary.get("gated_out", 0),
            summary.get("clusters", 0),
            summary.get("collapsed", 0),
        )
        return summary
    finally:
        await client.disconnect()


# ── CLI ───────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Telegram user-account RAG ingester")
    ap.add_argument("--login", action="store_true", help="one-time interactive session bootstrap")
    ap.add_argument("--cycle", action="store_true", help="run one ingestion sweep")
    ap.add_argument("--inventory", action="store_true", help="print categorized channel inventory")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    async def _login() -> None:
        client = await _get_client()
        await client.connect()
        if not await client.is_user_authorized():
            await client.start()  # prompts for phone + code interactively
        me = await client.get_me()
        print(f"Authorized as: {me.first_name} (@{me.username})")
        inv = await _enumerate_channels(client)
        print(f"{len(inv)} channels/groups enumerated:")
        by_cat: dict[str, int] = defaultdict(int)
        for ch in inv:
            by_cat[ch["category"]] += 1
        for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            print(f"  {cat:12s} {n}")
        state = _load_state()
        state["inventory"] = inv
        _save_state(state)
        await client.disconnect()

    async def _inventory() -> None:
        state = _load_state()
        for ch in state.get("inventory", []):
            print(f"{ch['category']:12s} {ch['title'][:50]:52s} @{ch['username']}")

    if args.login:
        asyncio.run(_login())
    elif args.inventory:
        asyncio.run(_inventory())
    else:
        asyncio.run(run_cycle())


if __name__ == "__main__":
    main()
