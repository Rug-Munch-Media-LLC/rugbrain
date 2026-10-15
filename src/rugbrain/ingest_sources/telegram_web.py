"""Telegram public-channel ingestion via t.me/s/ web previews — no auth needed.

Same data-access method as the bot's telegram_news sweeper: public channel
preview pages at ``https://t.me/s/<channel>`` are plain HTML requiring no
Telegram session. Covers every PUBLIC channel in the owner's inventory
(450-channel sweep list built from the account's session cache audit).
Private groups additionally require the MTProto source (telegram_userbot.py).

Pipeline: fetch → dedupe (content hash + per-channel watermark) → importance
rerank → shill-campaign grouping → store_documents into news_v2 /
threat_intel_v2 (interim suffix applies automatically).
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
from typing import Any

import httpx

from rugbrain.ingest_sources import ingest_cycle
from rugbrain.ingest_sources.ingest_scoring import (
    CATEGORY_RULES,
    CATEGORY_WEIGHT,
    IMPORTANCE_KEYWORDS,
    TICKER_RE,
)
from rugbrain.ingest_sources.tg_preview import parse_preview_page

logger = logging.getLogger("rugbrain.tgweb")

CHANNELS_FILE = os.getenv("RAG_TGWEB_CHANNELS", "/var/lib/rugbrain/tg_channels.txt")
STATE_FILE = os.getenv("RAG_TGWEB_STATE", "/var/lib/rugbrain/tgweb_state.json")
PER_CHANNEL = int(os.getenv("RAG_TGWEB_LIMIT", "20"))
MAX_PER_CYCLE = int(os.getenv("RAG_TGWEB_MAX_CHANNELS", "80"))
UA = os.getenv(
    "RAG_TGWEB_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
)

_MSG_RE = re.compile(
    r'<div class="tgme_widget_message[^"]*"[^>]*data-post="(?P<channel>[^/"]+)/(?P<msg_id>\d+)"(?P<body>.*?)'
    r'<div class="tgme_widget_message_footer',
    re.DOTALL,
)
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<text>.*?)</div>', re.DOTALL
)
_TIME_RE = re.compile(r'<time[^>]*datetime="(?P<dt>[^"]+)"')


def categorize(channel: str) -> str:
    blob = channel.lower()
    for cat, needles in CATEGORY_RULES:
        if any(n in blob for n in needles):
            return cat
    return "general"


def importance(text: str, category: str, echo: int) -> float:
    t = text.lower()
    kw = sum(w for k, w in IMPORTANCE_KEYWORDS.items() if k in t)
    return round(
        min(kw, 6.0) * CATEGORY_WEIGHT.get(category, 0.7) * (1.0 + 0.25 * max(0, echo - 1)), 2
    )


def detect_campaigns(msgs: list[dict[str, Any]], min_channels: int = 4) -> list[dict[str, Any]]:
    by_token: dict[str, set[str]] = defaultdict(set)
    sample: dict[str, str] = {}
    cutoff = time.time() - 90 * 60
    for m in msgs:
        if m.get("_epoch", 0) and m["_epoch"] < cutoff:
            continue
        for tok in TICKER_RE.findall(m["text"]):
            tl = tok.lower()
            by_token[tl].add(m["channel"])
            sample.setdefault(tl, m["text"][:300])
    return [
        {"token": tok.upper(), "channels": sorted(chans), "sample": sample[tok]}
        for tok, chans in by_token.items()
        if len(chans) >= min_channels
    ]


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)  # type: ignore[no-any-return]  # json.load returns Any
    except (OSError, json.JSONDecodeError):
        return {"channels": {}, "seen_hashes": []}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    hashes = state.get("seen_hashes", [])
    state["seen_hashes"] = hashes[-20000:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def load_channel_list() -> list[str]:
    try:
        with open(CHANNELS_FILE) as f:
            chans = [ln.strip().lstrip("@") for ln in f if ln.strip() and not ln.startswith("#")]
        return sorted(set(chans))
    except OSError:
        return []


async def run_cycle(max_channels: int = MAX_PER_CYCLE) -> dict[str, Any]:
    started = time.time()
    channels = load_channel_list()
    if not channels:
        logger.warning("tgweb_no_channels file=%s", CHANNELS_FILE)
        return {"error": "no channel list"}

    state = _load_state()
    order = sorted(channels)
    last_swept = int(state.get("_rr_index", 0))
    batch = [order[(last_swept + i) % len(order)] for i in range(min(max_channels, len(order)))]
    state["_rr_index"] = (last_swept + len(batch)) % max(1, len(order))

    summary: dict[str, Any] = {
        "channels": len(batch),
        "messages": 0,
        "unique_docs": 0,
        "campaigns": 0,
        "ingested": 0,
    }
    all_msgs: list[dict[str, Any]] = []
    headers = {"User-Agent": UA}

    async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
        sem = asyncio.Semaphore(6)

        async def sweep(ch: str) -> None:
            async with sem:
                try:
                    r = await client.get(f"https://t.me/s/{ch}")
                    if r.status_code != 200:
                        return
                    msgs = parse_preview_page(r.text, ch)
                except Exception as e:
                    logger.debug("tgweb_fetch_failed ch=%s err=%s", ch, str(e)[:60])
                    return
                last_id = int(state["channels"].get(ch, {}).get("last_msg_id", 0))
                fresh = [m for m in msgs if m["msg_id"] > last_id]
                if fresh:
                    state["channels"][ch] = {"last_msg_id": max(m["msg_id"] for m in fresh)}
                    all_msgs.extend(fresh)

        await asyncio.gather(*(sweep(c) for c in batch))
    summary["messages"] = len(all_msgs)

    echo: dict[str, int] = defaultdict(int)
    for m in all_msgs:
        m["chash"] = hashlib.sha256(m["text"].lower().encode()).hexdigest()[:16]
        echo[m["chash"]] += 1
    # Dedupe against the bounded ring, then the crypto-relevance gate. This path
    # writes the bulk of news_v2 and had no gate at all, which is how war
    # channels and forex spam reached a crypto collection. Shared with the
    # userbot path via ingest_cycle so the policy cannot drift.
    fresh, state["seen_hashes"], gate_stats = ingest_cycle.gate_and_bound(
        all_msgs,
        seen_hashes=state.get("seen_hashes", []),
        get_text=lambda m: m["text"],
        get_category=lambda m: categorize(m["channel"]),
        get_hash=lambda m: m["chash"],
    )
    summary.update(gate_stats)

    campaigns = detect_campaigns(fresh)
    docs: list[dict[str, Any]] = []
    for m in fresh:
        cat = categorize(m["channel"])
        imp = importance(m["text"], cat, echo[m["chash"]])
        sev = (
            "critical" if imp >= 6 else "high" if imp >= 3.5 else "medium" if imp >= 1.5 else "low"
        )
        docs.append(
            {
                "content": f"[TG/{cat}] {m['channel']}: {m['text'][:4000]}",
                # Social-origin chatter -> social_intel (operator 2026-10-15);
                # only critical/high severity escalates to threat_intel.
                "collection": "threat_intel_v2" if sev in ("critical", "high") else "social_intel",
                "severity": sev,
                "source": f"t.me/{m['channel']}",
                "metadata": {
                    "kind": "telegram_message",
                    "category": cat,
                    "importance": imp,
                    "echo_count": echo[m["chash"]],
                    "msg_id": m["msg_id"],
                    "ts": m["ts"],
                },
            }
        )
    for c in campaigns:
        docs.append(
            {
                "content": (
                    f"[SHILL CAMPAIGN] ${c['token']} pushed across {len(c['channels'])} channels "
                    f"in 90min: {', '.join(c['channels'][:12])}. Sample:\n{c['sample']}"
                ),
                "collection": "threat_intel_v2",
                "severity": "critical" if len(c["channels"]) >= 8 else "high",
                "source": "t.me:shill_detector",
                "metadata": {
                    "kind": "shill_campaign",
                    "token": c["token"],
                    "channel_count": len(c["channels"]),
                },
            }
        )
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

        class _E:
            async def embed_one(self, text: str) -> list[float]:
                return await get_embedding(text)

        res = await store_documents(crawl_docs, _E(), await get_qdrant_store())
        summary["ingested"] = res.get("inserted", 0)

    _save_state(state)
    summary["seconds"] = round(time.time() - started, 1)
    logger.info(
        "tgweb_cycle_done channels=%d msgs=%d unique=%d campaigns=%d ingested=%d",
        summary["channels"],
        summary["messages"],
        summary["unique_docs"],
        summary["campaigns"],
        summary["ingested"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(asyncio.run(run_cycle()))
