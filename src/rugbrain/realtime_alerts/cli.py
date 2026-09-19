"""Command-line entry points for the realtime exploit monitor."""

from __future__ import annotations

import asyncio
import json

import httpx

from .models import CriticalAlert


async def _run_once() -> None:
    """Single detection pass (cron mode) — poll once and drain the pipeline."""
    from rugbrain import realtime_alerts as _pkg

    client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        all_candidates: list[CriticalAlert] = []
        all_candidates.extend(await _pkg.poll_searxng_exploits(client))
        all_candidates.extend(await _pkg.poll_onchain_anomalies())
        all_candidates.extend(await _pkg.poll_rpc_large_txs(client))
        for alert in all_candidates:
            await _pkg._monitor._handle_candidate(alert)
        print(
            json.dumps(
                {
                    "candidates": len(all_candidates),
                    "fired": _pkg._monitor.stats()["alerts_fired"],
                    "stats": _pkg._monitor.stats(),
                },
                indent=2,
            )
        )
    finally:
        await client.aclose()


def main() -> None:
    """CLI: ``--once`` runs a single pass, otherwise run forever."""
    import argparse

    from rugbrain import realtime_alerts as _pkg

    parser = argparse.ArgumentParser(description="RugBrain real-time exploit monitor")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()
    if args.once:
        asyncio.run(_run_once())
    else:
        asyncio.run(_pkg._monitor.run_forever())
