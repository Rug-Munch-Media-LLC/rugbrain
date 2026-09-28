"""HTML parsing for t.me/s public channel previews.

Split out of :mod:`rugbrain.ingest_sources.telegram_web` to keep that module
inside the 200-line limit, and because "turn a Telegram preview page into
messages" is a self-contained parsing problem that deserves its own tests.

t.me/s returns a server-rendered page; messages live in a
``tgme_widget_message`` container with the text in a nested ``<div>``. The
markup is scraped rather than parsed with a real HTML library because the
structure is stable and a dependency would be heavier than the problem.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
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
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<text>.*?)</div>', re.DOTALL
)
_TIME_RE = re.compile(r'<time[^>]*datetime="(?P<dt>[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
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
