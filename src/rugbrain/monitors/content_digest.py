"""Content Digest — ranked daily/hourly digest of crypto signals."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)
POLL = int(os.getenv("CONTENT_DIGEST_INTERVAL", "1800"))
SW: dict[str, int] = {
    "whale": 5,
    "liquidation": 5,
    "defi": 3,
    "social": 3,
    "news": 3,
    "podcast": 2,
    "video": 2,
    "market": 2,
    "mev": 2,
    "twitter": 2,
    "distribution": 2,
}
CAT: dict[str, str] = {
    "whale": "🐋 Whale Activity",
    "liquidation": "💀 Liquidations",
    "news": "📰 News",
    "market": "📊 Market Health",
    "social": "🔥 Social Buzz",
    "defi": "🏦 DeFi",
    "distribution": "📈 Token Distribution",
    "mev": "⚡ MEV",
    "twitter": "🐦 Twitter/X",
}
_D: dict[str, Any] = {"daily": [], "hourly": [], "at": None, "srcs": []}


def _mk(
    s: str,
    c: str,
    t: str,
    coin: str = "",
    hrs: float = 24,
    usd: float = 0,
    sd: float = 0,
    **kw: Any,
) -> dict[str, Any]:
    m = max(0, math.log10(max(usd, 1))) if usd > 0 else abs(sd) / 10 if sd else 0
    return {
        "s": s,
        "c": c,
        "cl": CAT.get(c, c),
        "t": t,
        "coin": coin.upper() if coin else "",
        "h": round(hrs, 1),
        "u": usd,
        "sd": sd,
        "sc": round(SW.get(s, 1) * 20 + max(0, (24 - hrs) * 2) + m, 1),
        "x": kw,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


async def g_hl() -> list[dict[str, Any]]:
    try:
        from rugbrain.monitors.hyperliquid_monitor import hyperliquid_full_cycle as f

        d = await f()
    except Exception:
        return []
    r: list[dict[str, Any]] = []
    n = time.time()
    for t in d.get("biggest_trades", []):
        c = t["coin"]
        h = (n - t["time"] / 1000) / 3600 if t.get("time") else 24
        r.append(
            _mk(
                "whale",
                "whale",
                f"{t.get('side', '')} {c} ${t['usd_value']:,.0f}",
                coin=c,
                hrs=h,
                usd=t["usd_value"],
            )
        )
    for li in d.get("recent_liquidations", []):
        c = li["coin"]
        h = (n - li["time"] / 1000) / 3600 if li.get("time") else 24
        r.append(
            _mk(
                "liquidation",
                "liquidation",
                f"💀 {c} ${li['usd_value']:,.0f}",
                coin=c,
                hrs=h,
                usd=li["usd_value"],
            )
        )
    for f2 in d.get("funding_snapshot", []):
        r.append(
            _mk(
                "market",
                "market",
                f"⚠️ Funding {f2['coin']}: {f2.get('rate_pct', 0)}%",
                coin=f2["coin"],
                hrs=0.5,
            )
        )
    return r


async def g_sc() -> list[dict[str, Any]]:
    try:
        from rugbrain.monitors.social_sentiment import get_social_sentiment_state as f

        s = f()
    except Exception:
        return []
    r = [
        _mk(
            "social",
            "social",
            f"📈 Spike {c['symbol']} ({c.get('ratio', 0)}x)",
            coin=c.get("symbol", ""),
            hrs=1,
            sd=c.get("ratio", 0) * 10,
        )
        for c in s.get("volume_spikes", [])[:5]
    ]
    r += [
        _mk("social", "social", f"🔥 {c['symbol']} ({v}/100)", coin=c["symbol"], hrs=2, sd=v - 50)
        for c in s.get("top_by_sentiment", [])[:5]
        if (v := c.get("sentiment_score", 50)) > 70
    ]
    r += [
        _mk("social", "social", f"💀 {c['symbol']} ({v}/100)", coin=c["symbol"], hrs=2, sd=v - 50)
        for c in s.get("bottom_by_sentiment", [])[:5]
        if (v := c.get("sentiment_score", 50)) < 30
    ]
    return r


async def g_lq() -> list[dict[str, Any]]:
    try:
        from rugbrain.liquidation_heatmap import liquidation_scan_cycle as f

        d = await f()
    except Exception:
        return []
    r: list[dict[str, Any]] = []
    if (v := d.get("volume_24h_usd", 0)) > 0:
        r.append(_mk("liquidation", "liquidation", f"💀 24h ${v:,.0f}", usd=v, hrs=1))
    r += [
        _mk(
            "liquidation",
            "liquidation",
            f"💀 {h.get('symbol', '?')} ${h['value_usd']:,.0f}",
            coin=h.get("symbol", "")[:3],
            usd=h["value_usd"],
            hrs=1,
        )
        for h in d.get("hype_liquidations", [])[:3]
    ]
    return r


async def g_ld() -> list[dict[str, Any]]:
    try:
        from rugbrain.monitors.defi_lending import get_defi_lending_state as f

        s = f()
    except Exception:
        return []
    r: list[dict[str, Any]] = []
    for pk, sn in s.get("protocols", {}).items():
        rk = sn.get("risk", {})
        u = rk.get("ltv_proximity_pct")
        if u and u > 80:
            r.append(_mk("defi", "defi", f"🏦 {sn.get('protocol', pk)} {u}% LTV", hrs=0.5))
        if rk.get("borrow_apy_risk") == "critical":
            r.append(_mk("defi", "defi", f"🏦 Borrow spike {sn.get('protocol', pk)}", hrs=0.5))
    return r


async def g_tw() -> list[dict[str, Any]]:
    try:
        from rugbrain.monitors.twitter_tracker import get_twitter_tracker_state as f

        s = f()
    except Exception:
        return []
    return [
        _mk(
            "twitter",
            "twitter",
            f"🐦 {a.get('author', '?')}: {a.get('text_preview', '')[:80]}",
            coin=(a.get("coins") or [""])[0],
            hrs=2,
        )
        for a in s.get("alerts", [])[:5]
    ]


async def g_dt() -> list[dict[str, Any]]:
    try:
        from rugbrain.monitors.token_distribution import get_whale_alerts as w
    except Exception:
        return []
    try:
        r = [
            _mk(
                "distribution",
                "distribution",
                a.get("message", ""),
                coin=a.get("symbol", ""),
                hrs=1,
                usd=abs(a.get("change_pct", 0)) * 10000,
            )
            for a in w(10)
        ]
        from rugbrain.monitors.token_distribution import get_concentration_ranking as rk

        r += [
            _mk(
                "distribution",
                "distribution",
                f"📈 Critical {r2['symbol']} ({r2['score']}/100)",
                coin=r2["symbol"],
                hrs=4,
            )
            for r2 in rk()[:3]
            if r2.get("risk") == "critical"
        ]
        return r
    except Exception:
        return []


async def g_mk() -> list[dict[str, Any]]:  # fear&greed + peg deviations + exchange flows
    r: list[dict[str, Any]] = []
    try:
        from rugbrain.fear_index import get_fear_index as f_fg

        fi = await f_fg()
    except Exception:
        fi = None
    if fi:
        v = fi.get("value", 50)
        label = fi.get("classification", "neutral")
        if v < 25 or v > 75:
            r.append(
                _mk(
                    "market",
                    "market",
                    f"{'😱' if v < 25 else '🤑'} F&G: {v}/100 ({label})",
                    hrs=0.5,
                    sd=v - 50,
                )
            )
    try:
        from rugbrain.market_health import market_health_cycle as f_mh

        d = await f_mh()
    except Exception:
        d = None
    if d:
        r += [
            _mk("market", "market", f"⚠️ {c} peg: {d2}%", coin=c, hrs=0.5, usd=d2 * 1000000)
            for c, d2 in d.get("peg_deviations_pct", {}).items()
            if d2 > 0.2
        ]
    try:
        from rugbrain.exchange_flow import exchange_flow_cycle as f_ef

        e = await f_ef()
    except Exception:
        e = None
    if e:
        r += [
            _mk(
                "market",
                "market",
                f"🏦 Withdrawals {x['exchange']} ({x.get('large_txns_24h', 0)})",
                hrs=1,
                usd=x.get("large_txns_24h", 0) * 250000,
            )
            for x in e.get("exchanges", [])
            if x.get("large_txns_24h", 0) >= 3
        ]
    return r


async def g_mv() -> list[dict[str, Any]]:
    try:
        from rugbrain.mev_detector import mev_scan_cycle as f

        d = await f()
    except Exception:
        return []
    return (
        [_mk("mev", "mev", f"⚡ {d.get('flashbots_mevs', 0)} MEV attacks", hrs=0.5)]
        if d.get("flashbots_mevs", 0)
        else []
    )


async def g_me() -> list[dict[str, Any]]:  # youtube + podcast + newsletter
    r: list[dict[str, Any]] = []
    yt: list[tuple[str, str, str]] | None
    try:
        from rugbrain.youtube_ingester import YT_CHANNELS as yt, get_channel_videos as gv
    except Exception:
        yt = None
    if yt:
        for name, u, _chan in yt[:5]:
            try:
                for v in await gv(u, 2):
                    r.append(
                        _mk(
                            "video",
                            "news",
                            f"🎬 {v.get('title', '')[:80]}",
                            hrs=6,
                            ch=name,
                            url=v.get("url", ""),
                        )
                    )
            except Exception:
                pass
    p: list[dict[str, str]] | None
    try:
        from rugbrain.podcast_ingester import CRYPTO_PODCASTS as p
    except Exception:
        p = None
    if p:
        r += [_mk("podcast", "news", f"🎙️ New: {p2['name']}", hrs=8) for p2 in p[:5]]
    nl: list[tuple[str, str, str, str]] | None
    try:
        from rugbrain.newsletter_ingester import NEWSLETTERS as nl
    except Exception:
        nl = None
    if nl:
        r += [_mk("news", "news", f"📰 {n} ({c})", hrs=6) for n, u, f, c in nl[:10] if f == "daily"]
    return r


GG: list[tuple[Any, str]] = [
    (g_hl, "hl"),
    (g_sc, "sc"),
    (g_lq, "lq"),
    (g_ld, "ld"),
    (g_tw, "tw"),
    (g_dt, "dt"),
    (g_mk, "mk"),
    (g_mv, "mv"),
    (g_me, "me"),
]


async def _agg() -> tuple[list[dict[str, Any]], list[str]]:
    rs = await asyncio.gather(*[fn() for fn, _ in GG], return_exceptions=True)
    sigs: list[dict[str, Any]] = []
    src: list[str] = []
    for rv, (_, lb) in zip(rs, GG):
        if isinstance(rv, BaseException):
            continue
        sigs.extend(rv)
        src.append(lb)
    cc: dict[str, int] = defaultdict(int)
    for s in sigs:
        if c := s.get("coin", ""):
            cc[c] += 1
    for s in sigs:
        n = cc.get(s.get("coin", ""), 0)
        s["cr"] = n
        s["sc"] = round(s["sc"] + n * 15, 1)
    sigs.sort(key=lambda s: s["sc"], reverse=True)

    # Use reranker to refine top-50 ranking if available
    try:
        from rugbrain.reranker import rerank as _rerank

        top50 = [s for s in sigs[:50] if s.get("t", "") or s.get("d", "")]
        if top50 and len([s for s in top50 if len(s.get("t", "") or "") > 10]) >= 3:
            query = "Most important crypto market signal right now"
            docs = [f"{s.get('t', '')}: {str(s.get('d', ''))[:200]}" for s in top50]
            rr = await _rerank(query, docs, top_k=len(docs), raw=True)
            if rr and len(rr) == len(top50):
                ranks = {r["index"]: r["relevance_score"] for r in rr}
                for i, s in enumerate(top50):
                    s["rr"] = ranks.get(i, 0.0)
                    s["sc"] = round(s["sc"] * 0.6 + ranks.get(i, 0.0) * 40, 1)
                sigs.sort(key=lambda s: s["sc"], reverse=True)
    except Exception:
        log.debug("reranker_skip")

    return sigs, src


async def generate_daily() -> list[dict[str, Any]]:
    sigs, src = await _agg()
    t20 = sigs[:20]
    bc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cc20: dict[str, int] = defaultdict(int)
    for s in t20:
        bc[s["c"]].append(s)
    for s in t20:
        if c := s.get("coin", ""):
            cc20[c] += 1
    r: dict[str, Any] = {
        "type": "daily",
        "ts": datetime.now(timezone.utc).isoformat(),
        "n": len(sigs),
        "s": src,
        "top": t20,
        "bc": {k: v for k, v in sorted(bc.items())},
        "cc": {c: n for c, n in sorted(cc20.items(), key=lambda x: -x[1]) if n > 1},
    }
    _D["daily"] = r
    _D["at"] = r["ts"]
    _D["srcs"] = src
    return cast_list(r["top"])


async def generate_hourly() -> list[dict[str, Any]]:
    sigs, src = await _agg()
    urgent = [
        s for s in sigs if s.get("u", 0) > 500000 or s["c"] in ("whale", "liquidation", "mev")
    ] or sigs
    seen: set[str] = set()
    d: list[dict[str, Any]] = []
    for s in urgent:
        k = f"{s['c']}:{s['t'][:40]}"
        if k not in seen:
            seen.add(k)
            d.append(s)
    d.sort(key=lambda s: s["sc"], reverse=True)
    t5 = d[:5]
    r: dict[str, Any] = {
        "type": "hourly",
        "ts": datetime.now(timezone.utc).isoformat(),
        "n": len(sigs),
        "s": src,
        "urgent": t5,
    }
    _D["hourly"] = r
    return cast_list(r["urgent"])


def cast_list(v: Any) -> list[dict[str, Any]]:
    """Narrow the cached digest payload back to its runtime type."""
    return list(v) if isinstance(v, list) else []


def by_coin(coin: str) -> list[dict[str, Any]]:
    coin = coin.upper()
    daily = _D.get("daily")
    if isinstance(daily, dict) and daily.get("top"):
        return [s for s in daily["top"] if s.get("coin", "") == coin]
    fallback = _D.get("by_coin", {})
    if isinstance(fallback, dict):
        out = fallback.get(coin, [])
        return out if isinstance(out, list) else []
    return []


def get_digest() -> dict[str, Any]:
    return dict(_D)


async def content_digest_loop() -> None:
    log.info("digest_started interval=%ds", POLL)
    while True:
        try:
            if datetime.now(timezone.utc).minute < 15:
                await generate_daily()
                log.info("digest_daily")
            else:
                await generate_hourly()
                log.info("digest_hourly")
        except Exception as exc:
            log.warning("digest_fail %s", str(exc)[:120])
        await asyncio.sleep(POLL)
