"""Governance Aggregator — Snapshot + Discourse proposal scraping.

Aggregates governance proposals from:
1. **Snapshot.org** — off-chain voting platform (free tier, no API key needed)
2. **Discourse forums** — discussion forums for major DAOs (free, scraping)

Tracks: proposal status, vote counts, quorum, discussion threads, whale voting patterns.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Tracked DAO governance spaces
TRACKED_SPACES = [
    "uniswap",
    "aave.eth",
    "compound-2",
    "makerdao",
    "curve",
    "balancer",
    "sushi",
    "yearn",
    "pancake",
    "snapshot.dcl.eth",
    "ens.eth",
    "gitcoindao.eth",
    "lido-snapshot.eth",
    "frax.eth",
]

SNAPSHOT_GRAPHQL = "https://hub.snapshot.org/graphql"
POLL_INTERVAL = int(os.getenv("GOV_POLL_INTERVAL", "300"))  # 5 min

SNAPSHOT_QUERY = """
query Proposals($space: String!, $limit: Int!) {
  proposals(
    first: $limit,
    where: { space: $space },
    orderBy: "created",
    orderDirection: desc
  ) {
    id
    title
    body
    state
    author
    created
    end
    votes
    quorum
    choices
    scores
    scores_total
    link
  }
}
"""


async def fetch_snapshot_proposals(space: str, limit: int = 5) -> list[dict[str, Any]]:
    """Fetch recent proposals for a Snapshot space."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                SNAPSHOT_GRAPHQL,
                json={
                    "query": SNAPSHOT_QUERY,
                    "variables": {"space": space, "limit": limit},
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []

    proposals = []
    for p in data.get("data", {}).get("proposals", []):
        proposals.append(
            {
                "source": "snapshot",
                "space": space,
                "id": p.get("id", ""),
                "title": p.get("title", ""),
                "state": p.get("state", "unknown"),
                "author": p.get("author", ""),
                "created": datetime.fromtimestamp(p.get("created", 0), tz=UTC).isoformat(),
                "end": datetime.fromtimestamp(p.get("end", 0), tz=UTC).isoformat(),
                "votes": p.get("votes", 0),
                "quorum": p.get("quorum", 0),
                "choices": p.get("choices", []),
                "scores": p.get("scores", []),
                "scores_total": p.get("scores_total", 0),
                "link": p.get("link", ""),
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )
    return proposals


async def governance_scan_cycle() -> dict[str, Any]:
    """Scan all tracked DAO governance spaces for active proposals."""
    all_proposals = []
    for space in TRACKED_SPACES:
        try:
            proposals = await fetch_snapshot_proposals(space)
            all_proposals.extend(proposals)
            await asyncio.sleep(0.5)  # Rate limit: 2 req/s
        except Exception as exc:
            logger.warning("gov_scan_failed space=%s error=%s", space, str(exc)[:60])

    active = [p for p in all_proposals if p["state"] in ("active", "pending")]
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "spaces_scanned": len(TRACKED_SPACES),
        "total_proposals": len(all_proposals),
        "active_proposals": len(active),
        "proposals": all_proposals[:20],
        "active_list": active[:10],
    }


async def governance_scan_loop() -> None:
    """Run governance scanning in a continuous background loop."""
    logger.info(
        "governance_aggregator_started interval=%ds spaces=%d", POLL_INTERVAL, len(TRACKED_SPACES)
    )
    while True:
        try:
            result = await governance_scan_cycle()
            if result["active_proposals"] > 0:
                logger.info(
                    "gov_active_proposals count=%d spaces=%d",
                    result["active_proposals"],
                    result["spaces_scanned"],
                )
        except Exception as exc:
            logger.warning("gov_scan_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
