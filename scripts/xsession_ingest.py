#!/usr/bin/env python3
"""X (Twitter) news ingest lane — session-cookie GraphQL, guest fallback.

Pulls each configured handle's timeline, writes new tweets to ``news_items``
(idempotent: partial unique index on ``tweet_id`` where ``source_type='x'``)
and can print the ranked feed using rmi-backend's canonical ranking formula.

    python3 scripts/xsession_ingest.py --once
    python3 scripts/xsession_ingest.py --once --handles lookonchain,zachxbt
    python3 scripts/xsession_ingest.py --rank

Env: X_COOKIE_PATH, POSTGRES_* (see src/rugbrain/xsession/config.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg
import httpx

from rugbrain.xsession import config
from rugbrain.xsession.api import timeline
from rugbrain.xsession.client import XAuthError, XSessionClient
from rugbrain.xsession.parse import XSchemaError
from rugbrain.xsession.rank import report_ranked
from rugbrain.xsession.store import count_x_rows, upsert_tweets

HANDLES_PATH = Path(__file__).resolve().parent.parent / "config" / "x_handles.json"
logger = logging.getLogger("xsession_ingest")


def load_handles(only: str | None = None) -> list[dict[str, Any]]:
    """Primary reader: the curated handle list, optionally filtered."""
    spec = json.loads(HANDLES_PATH.read_text(encoding="utf-8"))
    entries = spec.get("handles") or []
    if not only:
        return entries
    wanted = {h.strip().lower().lstrip("@") for h in only.split(",") if h.strip()}
    return [e for e in entries if str(e.get("handle", "")).lower() in wanted]


async def connect() -> asyncpg.Connection:
    """Postgres, configured host first then loopback (host + container alike)."""
    hosts = [os.getenv("POSTGRES_HOST", "127.0.0.1")]
    if hosts[0] not in ("127.0.0.1", "localhost"):
        hosts.append("127.0.0.1")
    last: Exception | None = None
    for host in hosts:
        try:
            return await asyncpg.connect(
                host=host,
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                user=os.getenv("POSTGRES_USER", "rmi"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
                database=os.getenv("POSTGRES_DB", "rmi"),
                timeout=8,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            last = exc
    raise RuntimeError(f"postgres unreachable: {last}")


async def ingest_handle(
    client: XSessionClient,
    conn: asyncpg.Connection,
    entry: dict[str, Any],
    max_pages: int,
) -> dict[str, Any]:
    """One handle → one report row. A failure is recorded, never raised."""
    handle = str(entry.get("handle") or "").strip().lstrip("@")
    klass = str(entry.get("class") or "news")
    trust = config.SOURCE_TRUST.get(klass, config.SOURCE_TRUST["news"])
    row: dict[str, Any] = {"handle": handle, "class": klass, "status": "ok"}
    try:
        user, tweets = await timeline(client, handle, max_pages=max_pages)
        row.update(
            user_id=user["user_id"],
            followers=user["followers"],
            protected=user["protected"],
            display=user.get("name"),
        )
        row.update(await upsert_tweets(conn, handle, tweets, klass, trust))
    except XSchemaError as exc:
        row.update(status="missing", error=str(exc)[:120])
    except XAuthError as exc:
        row.update(status="auth_error", error=str(exc)[:120])
    except httpx.HTTPError as exc:
        row.update(status="transport_error", error=type(exc).__name__)
    return row


def print_report(results: list[dict[str, Any]], totals: dict[str, Any]) -> None:
    """Per-handle table + run totals (all numbers come from the database)."""
    head = (
        f"{'handle':<18} {'class':<10} {'user_id':<21} {'fol':>9} "
        f"{'got':>4} {'new':>4} {'dup':>4}  status"
    )
    print(head)
    print("-" * len(head))
    for row in results:
        print(
            f"{row['handle']:<18} {row['class']:<10} {str(row.get('user_id', '-')):<21} "
            f"{row.get('followers', 0):>9} {row.get('fetched', 0):>4} "
            f"{row.get('inserted', 0):>4} {row.get('duplicate', 0):>4}  "
            f"{row['status']}{(' ' + row['error']) if row.get('error') else ''}"
        )
    ok = [r for r in results if r["status"] == "ok"]
    print(
        f"\nhandles={len(results)} ok={len(ok)} missing={len(results) - len(ok)} "
        f"fetched={sum(r.get('fetched', 0) for r in results)} "
        f"inserted={sum(r.get('inserted', 0) for r in results)} "
        f"duplicate={sum(r.get('duplicate', 0) for r in results)}"
    )
    print(f"db: total_x_rows={totals['total_x_rows']} last_hour_distinct={totals['last_hour_distinct']}")


async def run_once(handles: list[dict[str, Any]], max_pages: int) -> int:
    """Primary entry point: ingest every handle once, then report."""
    conn = await connect()
    results: list[dict[str, Any]] = []
    try:
        async with XSessionClient() as client:
            for entry in handles:
                results.append(await ingest_handle(client, conn, entry, max_pages))
            rate = client.rate
        names = [str(e["handle"]) for e in handles]
        totals = await count_x_rows(conn, names)
        print_report(results, totals)
        print("rate-limit state:", json.dumps(rate, sort_keys=True))
    finally:
        await conn.close()
    return 0 if any(r["status"] == "ok" for r in results) else 1


async def run_rank(limit: int) -> int:
    conn = await connect()
    try:
        await report_ranked(conn, limit)
    finally:
        await conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="X news ingest lane")
    parser.add_argument("--once", action="store_true", help="ingest every handle once")
    parser.add_argument("--rank", action="store_true", help="print the ranked feed")
    parser.add_argument("--handles", help="comma-separated subset of handles")
    parser.add_argument("--max-pages", type=int, default=config.MAX_TIMELINE_PAGES)
    parser.add_argument("--limit", type=int, default=10, help="rows for --rank")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    # httpx logs every request URL at INFO; the run report is the record we keep.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.rank:
        return asyncio.run(run_rank(args.limit))
    if args.once:
        return asyncio.run(run_once(load_handles(args.handles), args.max_pages))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
