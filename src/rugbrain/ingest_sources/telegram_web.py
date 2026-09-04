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
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("rugbrain.tgweb")

CHANNELS_FILE = os.getenv("RAG_TGWEB_CHANNELS", "/var/lib/rmi-rag/tg_channels.txt")
STATE_FILE = os.getenv("RAG_TGWEB_STATE", "/var/lib/rmi-rag/tgweb_state.json")
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
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", text).strip()


def parse_preview_page(html: str, channel: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _MSG_RE.finditer(html):
        body = m.group("body")
        tm = _TEXT_RE.search(body)
        if not tm:
            continue
        text = _clean(tm.group("text"))
        if len(text) < 30:
            continue
        dtm = _TIME_RE.search(body)
        try:
            dt = datetime.fromisoformat(dtm.group("dt")) if dtm else None
        except ValueError:
            dt = None
        out.append(
            {
                "channel": m.group("channel") or channel,
                "msg_id": int(m.group("msg_id")),
                "text": text,
                "ts": (dt or datetime.now(UTC)).isoformat(),
            }
        )
    return out


CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "scam_caller",
        ("scam", "fraud", "call out", "callout", "expose", "rug alert", "honeypot", "rugpull"),
    ),
    (
        "shiller",
        ("shill", "alpha caller", "calls", "100x", "gem", "moonshot", "degen", "ape", "pump"),
    ),
    (
        "security",
        ("security", "exploit", "hack", "certik", "blocksec", "cisa", "phishing", "audit"),
    ),
    (
        "news",
        (
            "news",
            "journal",
            "times",
            "post",
            "wire",
            "daily",
            "broadcast",
            "herald",
            "media",
            "feed",
            "digest",
        ),
    ),
    (
        "data",
        (
            "data",
            "analytics",
            "onchain",
            "on-chain",
            "whale",
            "defi",
            "tvl",
            "metrics",
            "scanner",
            "tracker",
        ),
    ),
    (
        "market",
        (
            "market",
            "trading",
            "signals",
            "futures",
            "perps",
            "btc",
            "eth",
            "solana",
            "crypto",
            "coin",
            "token",
        ),
    ),
]
CATEGORY_WEIGHT = {
    "scam_caller": 1.4,
    "security": 1.35,
    "shiller": 1.1,
    "news": 1.0,
    "data": 0.95,
    "market": 0.8,
    "general": 0.7,
}
IMPORTANCE_KEYWORDS = {
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
}
TICKER_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,15}\b")
CA_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")


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
    seen_ring = set(state.get("seen_hashes", []))
    fresh = [m for m in all_msgs if m["chash"] not in seen_ring]
    state["seen_hashes"] = list(seen_ring | {m["chash"] for m in fresh})

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
                "collection": "threat_intel_v2" if sev in ("critical", "high") else "news_v2",
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
