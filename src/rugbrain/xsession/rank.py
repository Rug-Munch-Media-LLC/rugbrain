"""Ranking proof — runs rmi-backend's canonical news ranking formula.

The formula lives in ``rmi-backend/app/domains/news/ranking.py``; it is loaded
by path (stdlib-only, no app settings) so the rank shown here is byte-for-byte
the ranking the feed API uses, not a re-implementation.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg

DEFAULT_BACKEND = "/srv/work/repos/rmi-backend"


def load_ranking(path: str | None = None) -> ModuleType:
    """Primary loader: import rmi-backend's ranking module by path."""
    root = Path(path or os.getenv("RMI_BACKEND_PATH", DEFAULT_BACKEND))
    target = root / "app" / "domains" / "news" / "ranking.py"
    if not target.is_file():
        raise FileNotFoundError(f"canonical ranking module not found: {target}")
    spec = importlib.util.spec_from_file_location("rmi_news_ranking", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {target}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def fetch_rows(conn: asyncpg.Connection, hours: int = 168, cap: int = 600) -> list[dict[str, Any]]:
    """Newest X rows inside the window, as dicts for the ranker."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    records = await conn.fetch(
        "SELECT news_id, url, title, summary, source, published_at, source_trust, "
        "category, tweet_id FROM news_items WHERE source_type='x' "
        "AND tweet_id IS NOT NULL AND published_at >= $1 "
        "ORDER BY published_at DESC LIMIT $2",
        since,
        cap,
    )
    return [dict(r) for r in records]


def format_top(rows: list[dict[str, Any]], limit: int = 10) -> str:
    """Render the ranked feed as a fixed-width table."""
    lines = [f"{'#':>2}  {'score':>8}  {'trust':>5}  {'recency':>7}  {'source':<18} {'published':<17} title"]
    for index, row in enumerate(rows[:limit], start=1):
        comp = row.get("score_components") or {}
        published = row.get("published_at")
        stamp = published.strftime("%Y-%m-%d %H:%M") if hasattr(published, "strftime") else "-"
        lines.append(
            f"{index:>2}  {row.get('score', 0):>8.5f}  {comp.get('source_trust', 0):>5.2f}  "
            f"{comp.get('recency_decay', 0):>7.3f}  {str(row.get('source'))[:18]:<18} "
            f"{stamp:<17} {str(row.get('title'))[:70]}"
        )
    return "\n".join(lines)


async def report_ranked(conn: asyncpg.Connection, limit: int = 10) -> dict[str, Any]:
    """Primary entry point: fetch → rank with the canonical formula → print."""
    ranking = load_ranking()
    rows = await fetch_rows(conn)
    if not rows:
        print("rank: no x rows in window")
        return {"rows": 0}
    scored = ranking.rank_items(rows, datetime.now(UTC))
    collapsed = len(scored)
    print(f"rank: {len(rows)} candidate rows → {collapsed} after title collapse + source diversity")
    print(format_top(scored, limit))
    return {"rows": len(rows), "ranked": collapsed, "top": scored[:limit]}
