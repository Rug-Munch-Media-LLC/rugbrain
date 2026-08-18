"""Twitter/X influencer tracker — Nitter RSS primary, SearXNG fallback, 10-min loop."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)
POLL_INTERVAL = int(os.getenv("TWITTER_TRACKER_INTERVAL", "600"))
NITTER_TIMEOUT = float(os.getenv("NITTER_TIMEOUT", "10.0"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://talos:8089")
SEARXNG_TIMEOUT = float(os.getenv("SEARXNG_TIMEOUT", "15.0"))
TOP5_ALERT_SLOTS = {
    "cz_binance",
    "saylor",
    "VitalikButerin",
    "RaoulGMI",
    "APompliano",
    "zhusu",
    "Arthur_0x",
    "Kyle_Davis",
}
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.lacontrevoie.fr",
    "https://nitter.projectsegfau.lt",
    "https://nitter.woodland.cafe",
    "https://nitter.freedit.eu",
    "https://nitter.snowy.industries",
    "https://nitter.bus-hit.me",
]
CRYPTO_INFLUENCERS = {
    # ── Top Influencers (Tier 1) ──
    "cz_binance": "CZ (Binance)",
    "saylor": "Michael Saylor",
    "VitalikButerin": "Vitalik Buterin",
    "RaoulGMI": "Raoul Pal",
    "APompliano": "Anthony Pompliano",
    "CryptoCred": "CryptoCred",
    "dwr": "Dan Romero",
    "iamDCinvestor": "DCinvestor",
    "zhusu": "Su Zhu",
    "bitcoinerD": "David (HSB)",
    "100trillionUSD": "PlanB",
    "woonomic": "Willy Woo",
    "nic__carter": "Nic Carter",
    "MikeBelshe": "Mike Belshe",
    "Kyle_Davis": "Kyle Davis",
    "Arthur_0x": "Arthur Hayes",
    "brian_armstrong": "Brian Armstrong",
    "elonmusk": "Elon Musk",
    # ── KOLs & Traders (Tier 2) ──
    "CryptoMichNL": "CryptoMichNL",
    "TheCryptoLark": "Crypto Lark",
    "cryptomanran": "CryptomanRan",
    "AltcoinDailyio": "Altcoin Daily",
    "MilesDeutscher": "Miles Deutscher",
    "CryptoJelleNL": "Crypto Jelle",
    "rektcapital": "Rekt Capital",
    "dutchcrypto1": "Dutch Crypto",
    "MacroScope17": "MacroScope",
    "tedtalksmacro": "Ted Mac",
    "venturefounder": "VentureFounder",
    "HsakaTrades": "Hsaka",
    "SqueezeMetrics": "SqueezeMetrics",
    "DylanLeClair_": "Dylan LeClair",
    "pythianism": "Pythianism",
    "Pentoshi": "Pentoshi",
    "mikeyklaime": "Mikey Klaime",
    "Trader_XO": "Trader XO",
    "Loma_xyz": "Loma",
    "cryptowhale": "Crypto Whale",
    "CryptoWendyO": "Crypto Wendy",
    "OfficialCryptoO": "Crypto O",
    "TheWhiteLion1": "White Lion",
    "McKieTrader": "McKie Trader",
    # ── News / Media (Tier 3) ──
    "coinbase": "Coinbase",
    "binance": "Binance",
    "MessariCrypto": "Messari",
    "glassnode": "Glassnode",
    "DefiLlama": "DeFiLlama",
    "Uniswap": "Uniswap",
    "aave": "Aave",
    "CompoundFinance": "Compound",
    "Chainlink": "Chainlink",
    "Solana": "Solana",
    "ethereum": "Ethereum",
    "Bitcoin": "Bitcoin",
    "Cointelegraph": "CoinTelegraph",
    "CoinDesk": "CoinDesk",
    "TheBlock__": "The Block",
    "decryptmedia": "Decrypt",
    "Utoday_en": "U.Today",
    "CryptoPotato": "CryptoPotato",
    "NewsBTC": "NewsBTC",
    "beincrypto": "BeInCrypto",
    "watchernews": "Watcher News",
    "CryptoGlobe": "CryptoGlobe",
    "BitcoinMagazine": "Bitcoin Magazine",
    "Blockworks_": "Blockworks",
    "TheDefiant": "The Defiant",
    "BanklessHQ": "Bankless",
    "DLNewsInfo": "DL News",
    "Unchained_pod": "Unchained Podcast",
    "Empire_pod": "Empire Podcast",
    # ── KOL Scammers / Flagged Accounts (Tier 4 — monitored) ──
    "BitBoyX": "BitBoy (Ben Armstrong)",
    "benjewels420": "Ben Jewels",
    "CryptoGodJohn": "Crypto God John",
    "CryptoSoul_": "CryptoSoul",
    "CryptoRox_": "CryptoRox",
    "CryptoKeeper_": "Crypto Keeper",
    "TheCryptoCPA": "Crypto CPA",
    "CryptoGemTrader": "Crypto Gem Trader",
    "CryptoGoes_": "Crypto Goes",
    "Crypt0_bear": "Crypto Bear",
    "CryptoCapo_": "Crypto Capo",
    "FloodMonk_": "Flood Monk",
    # ── On-Chain Analysts (Tier 5) ──
    "zhangye_eth": "Zhang Ye",
    "Lookonchain": "Lookonchain",
    "ai_9684xtpa": "Ai 9684",
    "spotonchain": "Spot On Chain",
    "WhaleStats": "WhaleStats",
    "whale_alert": "Whale Alert",
    "Nansen_ai": "Nansen",
    "rektnews": "Rekt News",
    "PeckShieldAlert": "PeckShieldAlert",
    "CyversAlerts": "Cyvers",
    "CertiKAlert": "CertiK Alert",
}
COIN_RE = re.compile(
    r"\b(BTC|ETH|SOL|XRP|ADA|DOT|AVAX|MATIC|LINK|UNI|AAVE|COMP|CRV|SUSHI|CAKE|ATOM|NEAR|FTM|ALGO|VET|THETA|FIL|EOS|TRX|XLM|HBAR|ICP|EGLD|KSM|RUNE|LRC|ZIL|ONE|HOT|ENJ|MANA|SAND|AXS|CHZ|ANKR|CELO|AR|FTT|SRM|RAY|LUNC|LUNA|USTC|PEPE|DOGE|SHIB|FLOKI|BONK|WIF|PENG|TIA|SUI|APT|SEI|INJ|OP|ARB|STRK|ZK|WLD|AGIX|FET|OCEAN|RNDR|GRT|FIL|HNT|IOTX|DIONE|PRIME|PYTH|TNSR|DRIFT|IO|NOT|DOGS|NEIRO|GOAT|VVAIFU)\b"
)
PRICE_KEYS = {
    "price",
    "pump",
    "dump",
    "buy",
    "sell",
    "long",
    "short",
    "moon",
    "crash",
    "ATH",
    "bottom",
    "top",
    "support",
    "resistance",
    "breakout",
    "correction",
    "rally",
    "dip",
    "surge",
    "plunge",
    "soar",
    "tank",
    "liquidate",
    "liquidation",
    "bear",
    "bull",
    "FOMO",
    "FUD",
    "panic",
    "capitulation",
    "accumulate",
    "distribution",
    "scalp",
    "bag",
    "exit",
    "entry",
    "position",
    "margin",
    "leverage",
    "rug",
    "scam",
    "honeypot",
    "insider",
    "whale",
}
POS_WORDS = {
    "bullish",
    "moon",
    "pump",
    "breakout",
    "rally",
    "soar",
    "surge",
    "gain",
    "profit",
    "ATH",
    "adoption",
    "institutional",
    "partnership",
    "upgrade",
    "growth",
    "opportunity",
    "accumulate",
    "hodl",
    "strong",
    "promising",
    "innovation",
    "breakthrough",
}
NEG_WORDS = {
    "bearish",
    "dump",
    "crash",
    "tank",
    "plunge",
    "liquidation",
    "panic",
    "FUD",
    "scam",
    "hack",
    "exploit",
    "rug",
    "ban",
    "crackdown",
    "regulation",
    "fear",
    "capitulation",
    "reversal",
    "warning",
    "risk",
    "decline",
    "collapse",
    "withdraw",
    "freeze",
}
_tweets: deque[dict[str, Any]] = deque(maxlen=5000)
_recent_alerts: deque[dict[str, Any]] = deque(maxlen=100)
_trending: dict[str, Any] = {}
_seen: set[str] = set()
_health: dict[str, Any] = dict(last_poll=0.0, error="", fails=0, polls=0, status="init")


def _clean(raw: str) -> str:
    t = re.sub(r"<[^>]+>", "", raw)
    for e, c in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")]:
        t = t.replace(e, c)
    return t.strip()


def _eng(ct: str, em: str) -> int:
    m = re.search(re.escape(em) + r"\s*(\d[\d,.]*)", ct)
    return int(m.group(1).replace(",", "")) if m else 0


async def _nitter(h: str) -> str | None:
    for inst in NITTER_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=NITTER_TIMEOUT, follow_redirects=True) as c:
                r = await c.get(f"{inst}/{h}/rss", headers={"User-Agent": "RugBrain/1.0"})
                if r.status_code == 200 and len(r.text) > 200:
                    return r.text
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError):
            continue
    return None


def _sent(words: set[str]) -> str:
    p = sum(1 for w in POS_WORDS if w in words)
    n = sum(1 for w in NEG_WORDS if w in words)
    return "positive" if p > n else "negative" if n > p else "neutral"


def _tweet(h: str, text: str, url: str, pub: str, likes: int, rts: int, tid: str) -> dict[str, Any]:
    text = text[:500]
    words = set(text.lower().split())
    return dict(
        id=tid,
        handle=h,
        author=CRYPTO_INFLUENCERS.get(h, h),
        text=text,
        url=url,
        published=pub,
        likes=likes,
        retweets=rts,
        coins=sorted(set(COIN_RE.findall(text))),
        sentiment=_sent(words),
        engagement=likes + rts,
        high_engagement=likes > 1000 or bool(PRICE_KEYS & words),
        price_sensitive=bool(PRICE_KEYS & words),
        scraped_at=datetime.now(UTC).isoformat(),
    )


def _parse_rss(xml: str, h: str) -> list[dict[str, Any]]:
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL):
        ct = _clean(
            ((m := re.search(r"<content[^>]*>(.*?)</content>", entry, re.DOTALL)) and m.group(1))
            or ""
        )
        text = ct or _clean(
            ((m := re.search(r"<title>(.*?)</title>", entry, re.DOTALL)) and m.group(1)) or ""
        )
        url = ((m := re.search(r"<link.*?href=\"(.*?)\"", entry)) and m.group(1)) or ""
        pub = ((m := re.search(r"<published>(.*?)</published>", entry)) and m.group(1)) or ""
        tid = url.split("/")[-1] if "/" in url else str(hash(text))
        if f"{h}:{tid}" in _seen or not text.strip():
            continue
        _seen.add(f"{h}:{tid}")
        out.append(_tweet(h, text, url, pub, _eng(ct, "\u2764"), _eng(ct, "\ud83d\udd04"), tid))
    return out


async def _searxng(h: str) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as c:
            r = await c.get(
                f"{SEARXNG_URL}/search",
                params=dict(
                    q=f"site:twitter.com {h}",
                    format="json",
                    language="en",
                    categories="social media",
                ),
            )
            if r.status_code != 200:
                return []
            results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for res in results[:10]:
        text = f"{res.get('title', '')} {res.get('content', '')}"[:500]
        if not text.strip():
            continue
        dk = f"sx:{h}:{res.get('url', '')}"
        if dk in _seen:
            continue
        _seen.add(dk)
        t = _tweet(h, text, res.get("url", ""), res.get("publishedDate", ""), 0, 0, dk)
        t["source"] = "searxng"
        out.append(t)
    return out


def _trend(tweets: list[dict[str, Any]]) -> dict[str, Any]:
    coins: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    handles: Counter[str] = Counter()
    bc: dict[str, list[str]] = defaultdict(list)
    for t in tweets:
        for c in t.get("coins", []):
            coins[c] += 1
        handles[t["handle"]] += 1
        for w in t["text"].lower().split():
            if len(w) > 4 and w not in ("https", "http", "the", "this", "that", "with", "from"):
                keywords[w] += 1
    return dict(
        top_coins=coins.most_common(10),
        top_keywords=keywords.most_common(20),
        most_active=handles.most_common(10),
        total=len(tweets),
        at=datetime.now(UTC).isoformat(),
    )


def _alerts(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tweets:
        if not t.get("high_engagement"):
            continue
        out.append(
            dict(
                type="influencer_tweet",
                handle=t["handle"],
                author=t["author"],
                text_preview=t["text"][:200],
                url=t["url"],
                coins=t.get("coins", []),
                sentiment=t.get("sentiment", "neutral"),
                engagement=t.get("engagement", 0),
                severity="high" if t["handle"] in TOP5_ALERT_SLOTS else "medium",
                price_sensitive=t.get("price_sensitive", False),
                timestamp=datetime.now(UTC).isoformat(),
            )
        )
    for a in out:
        _recent_alerts.append(a)
    return out


async def _dispatch(alert: dict[str, Any]) -> None:
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        await dispatch_alert(
            title=f"\U0001f426 {alert['author']}: {alert['text_preview'][:80]}",
            message=(
                f"Handle: @{alert['handle']}\nCoins: {', '.join(alert.get('coins', [])) or 'n/a'}\nSentiment: {alert['sentiment']}\nEngagement: {alert['engagement']:,}\nSeverity: {alert['severity']}\nSource: X/Twitter Tracker"
            ),
            alert_type="twitter_influencer_alert",
            severity=alert["severity"],
        )
    except Exception:
        pass


async def twitter_tracker_cycle() -> dict[str, Any]:
    handles = list(CRYPTO_INFLUENCERS)
    nr = await asyncio.gather(*[_nitter(h) for h in handles], return_exceptions=True)
    nt: list[dict[str, Any]] = []
    fails = 0
    for h, res in zip(handles, nr):
        if isinstance(res, Exception) or res is None:
            fails += 1
            continue
        nt.extend(_parse_rss(res, h))
    st: list[dict[str, Any]] = []
    if fails:
        for h, r in zip(
            [h for h, r in zip(handles, nr) if isinstance(r, Exception) or r is None],
            await asyncio.gather(
                *[
                    _searxng(h)
                    for h in [
                        h for h, r in zip(handles, nr) if isinstance(r, Exception) or r is None
                    ]
                ],
                return_exceptions=True,
            ),
        ):
            if not isinstance(r, Exception) and r:
                st.extend(r)
    all_t = nt + st
    for t in all_t:
        _tweets.append(t)

    # Score tweets with reranker for scam detection
    try:
        from rugbrain.reranker import rerank_kol_tweets

        if all_t:
            scored = await rerank_kol_tweets([dict(t) for t in all_t[-50:]], top_k=5)
            _recent_alerts.clear()
            for t in scored:
                if t.get("_scam_score", 0) > 0.5:
                    _recent_alerts.append(
                        {
                            "type": "kol_scam",
                            "author": t.get("author", "unknown"),
                            "text": t.get("text", "")[:100],
                            "score": round(t["_scam_score"], 3),
                            "time": datetime.now(UTC).isoformat(),
                        }
                    )
                    # Alert if highly suspicious
                    if t["_scam_score"] > 0.7:
                        try:
                            from rugbrain.alert_dispatcher import dispatch_alert

                            asyncio.ensure_future(
                                dispatch_alert(
                                    title=f"\U0001f6a9 KOL Scam Alert: @{t.get('author', 'unknown')}",
                                    message=(
                                        f"Reranker scam score: {t['_scam_score']:.2f}\n"
                                        f"{t.get('text', '')[:200]}"
                                    ),
                                    alert_type="kol_scam",
                                    severity="high" if t["_scam_score"] > 0.85 else "medium",
                                )
                            )
                        except Exception:
                            pass
    except Exception:
        pass

    if all_t:
        _trending.update(_trend(all_t))
    alerts = _alerts(all_t)
    for a in alerts:
        await _dispatch(a)
    _health.update(last_poll=time.time(), fails=0, polls=_health["polls"] + 1, status="running")
    logger.info(
        "tw_cycle new=%d cached=%d nitter=%d searxng=%d alerts=%d",
        len(all_t),
        len(_tweets),
        len(nt),
        len(st),
        len(alerts),
    )
    return dict(
        scanned_at=datetime.now(UTC).isoformat(),
        accounts=len(handles),
        nitter_tweets=len(nt),
        searxng_tweets=len(st),
        new=len(all_t),
        cached=len(_tweets),
        alerts=len(alerts),
    )


async def twitter_tracker_health() -> dict[str, Any]:
    now = time.time()
    last = _health.get("last_poll", 0)
    ok = _health.get("status") == "running" and (now - last) < POLL_INTERVAL * 3
    return dict(
        name="twitter_influencer_tracker",
        ok=ok,
        status=_health.get("status", "unknown"),
        last_poll_ts=last,
        age_s=round(now - last, 1),
        failures=_health.get("fails", 0),
        polls=_health.get("polls", 0),
        accounts=len(CRYPTO_INFLUENCERS),
        cached=len(_tweets),
        recent_alerts=len(_recent_alerts),
        interval=POLL_INTERVAL,
    )


def get_twitter_tracker_state() -> dict[str, Any]:
    bh: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in _tweets:
        bh[t["handle"]].append(t)
    return dict(
        updated=datetime.now(UTC).isoformat(),
        health=_health,
        summary=dict(cached=len(_tweets), with_data=len(bh), tracked=len(CRYPTO_INFLUENCERS)),
        by_handle={
            h: dict(
                name=CRYPTO_INFLUENCERS.get(h, h),
                count=len(tl),
                last=tl[-1],
                avg_eng=sum(t.get("engagement", 0) for t in tl) // len(tl) if tl else 0,
            )
            for h, tl in sorted(bh.items())
        },
        trending=_trending,
        alerts=list(_recent_alerts)[-20:],
        recent=list(_tweets)[-50:],
    )


async def twitter_tracker_loop() -> None:
    """Background loop: poll Nitter/SearXNG every POLL_INTERVAL seconds.
    Import: from rugbrain.monitors.twitter_tracker import twitter_tracker_loop"""
    logger.info("tw_started interval=%ds accounts=%d", POLL_INTERVAL, len(CRYPTO_INFLUENCERS))
    while True:
        try:
            r = await twitter_tracker_cycle()
            if r["alerts"]:
                logger.info("tw_alerts count=%d", r["alerts"])
        except Exception as exc:
            _health["fails"] += 1
            _health["error"] = str(exc)[:200]
            logger.warning("tw_fail attempt=%d err=%s", _health["fails"], str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
