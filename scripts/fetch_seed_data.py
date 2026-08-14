#!/usr/bin/env python3
"""
Stage Postgres data for the Qdrant seeder (news_items, new_pairs, scan data).

Postgres is only reachable from inside the rmi-backend container (credentials
are injected there), so this script shells out to ``docker exec rmi-backend``
and writes JSON dumps to DATA_DIR (default /tmp/rag_seed). No credentials are
stored in this repository.

Usage:
    python scripts/fetch_seed_data.py

Outputs:
    {DATA_DIR}/news_items.json          — 1,000 most recent news articles
    {DATA_DIR}/new_pairs.json           — new trading pairs
    {DATA_DIR}/scan_community.json      — scan_results + scan_reports + community_submissions
    {DATA_DIR}/scamsniffer_addresses.json  — cached by seeder
    {DATA_DIR}/metamask_blacklist.json  — cached by seeder
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_seed_data")

# Staged data lives under a user-configurable directory (default /tmp); the
# directory is outside the repo and never contains credentials.
DATA_DIR = os.getenv("SEED_DATA_DIR", "/tmp/rag_seed")  # noqa: S108  # nosec B108
CONTAINER = os.getenv("RMI_BACKEND_CONTAINER", "rmi-backend")

PY_SNIPPETS = {
    "news_items.json": """
import asyncio, os, asyncpg, json
async def main():
    conn = await asyncpg.connect(host='rmi-postgres', port=5432, user='rmi',
        password=os.getenv('POSTGRES_PASSWORD',''), database='rmi', timeout=5)
    rows = await conn.fetch('''SELECT news_id, title, COALESCE(summary,'') AS summary,
        COALESCE(body_markdown,'') AS body, source, published_at, url
        FROM news_items WHERE COALESCE(summary,'') <> '' OR COALESCE(body_markdown,'') <> ''
        ORDER BY published_at DESC NULLS LAST LIMIT 1000''')
    out = [{'id': r['news_id'], 'title': r['title'] or '', 'summary': r['summary'] or '',
            'body': r['body'] or '', 'source': r['source'] or '',
            'published_at': str(r['published_at']) if r['published_at'] else '',
            'url': r['url'] or ''} for r in rows]
    print(json.dumps(out))
    await conn.close()
asyncio.run(main())
""",
    "new_pairs.json": """
import asyncio, os, asyncpg, json
async def main():
    conn = await asyncpg.connect(host='rmi-postgres', port=5432, user='rmi',
        password=os.getenv('POSTGRES_PASSWORD',''), database='rmi', timeout=5)
    rows = await conn.fetch('SELECT * FROM new_pairs')
    print(json.dumps({'new_pairs': [dict(r) for r in rows]}, default=str))
    await conn.close()
asyncio.run(main())
""",
    "scan_community.json": """
import asyncio, os, asyncpg, json
async def main():
    conn = await asyncpg.connect(host='rmi-postgres', port=5432, user='rmi',
        password=os.getenv('POSTGRES_PASSWORD',''), database='rmi', timeout=5)
    out = {}
    rows = await conn.fetch('SELECT * FROM scan_results')
    out['scan_results'] = [dict(r) for r in rows]
    rows = await conn.fetch('SELECT * FROM scan_reports')
    out['scan_reports'] = [dict(r) for r in rows]
    rows = await conn.fetch('SELECT * FROM community_submissions WHERE deleted_at IS NULL')
    out['community_submissions'] = [dict(r) for r in rows]
    print(json.dumps(out, default=str))
    await conn.close()
asyncio.run(main())
""",
}


def fetch_one(filename: str, snippet: str) -> None:
    """Run a snippet inside the backend container and save stdout to file."""
    path = os.path.join(DATA_DIR, filename)
    logger.info("Fetching %s ...", filename)
    try:
        # Intentional docker exec against the trusted rmi-backend container
        # (no shell interpolation; args are a fixed list).
        proc = subprocess.run(  # noqa: S603
            ["docker", "exec", CONTAINER, "python3", "-c", snippet],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        logger.error("%s docker exec failed: %s", filename, str(e)[:200])
        return
    if proc.returncode != 0:
        logger.error("%s failed: %s", filename, proc.stderr.strip()[-300:])
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(proc.stdout)
    logger.info("Saved %s (%d bytes)", filename, len(proc.stdout))


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    if "--check" in sys.argv:
        # Verify docker + container available
        try:
            r = subprocess.run(  # noqa: S603
                ["docker", "exec", CONTAINER, "python3", "-V"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode != 0:
                logger.error("container %s unavailable: %s", CONTAINER, r.stderr.strip()[:200])
                return 1
            logger.info("container %s available", CONTAINER)
        except Exception as e:
            logger.error("docker unavailable: %s", str(e)[:200])
            return 1
        return 0

    for filename, snippet in PY_SNIPPETS.items():
        fetch_one(filename, snippet)

    # Copy the etherscan CSVs into the data dir so the seeder is self-contained
    import shutil

    for name in ("etherscan_phish_hack.csv", "etherscan_combined_labels.csv"):
        src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..",
            "rmi-backend",
            "data",
            name,
        )
        if os.path.exists(src):
            dst = os.path.join(DATA_DIR, name)
            shutil.copy(src, dst)
            logger.info("Copied %s -> %s", name, dst)

    logger.info("Staging complete in %s", DATA_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
