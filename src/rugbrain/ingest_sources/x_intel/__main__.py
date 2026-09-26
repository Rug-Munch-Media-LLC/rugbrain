"""CLI for ``python -m rugbrain.ingest_sources.x_intel``.

Exit codes: ``0`` success, ``2`` usage/config error, ``3`` Pry lane
payment-gated (x402 402) — see ``pry_lane.PRY_LANE_EXIT_CODE``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

CycleFn = Callable[[], Awaitable[dict[str, Any]]]


def _run_cycle(cycle: CycleFn | None = None) -> int:
    """Run one cycle; translate a dead Pry lane into a nonzero exit.

    ``cycle`` is the DI seam: tests inject a stub (or a raiser) instead of
    patching ``pipeline.run_cycle``.
    """
    from rugbrain.ingest_sources.x_intel.pipeline import run_cycle
    from rugbrain.ingest_sources.x_intel.pry_paywall import PRY_LANE_EXIT_CODE, PryLaneUnavailable

    try:
        print(json.dumps(asyncio.run((cycle or run_cycle)()), indent=2))
    except PryLaneUnavailable as exc:
        print(f"x-intel ingest FAILED: {exc}", file=sys.stderr)
        return PRY_LANE_EXIT_CODE
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RugBrain X/Twitter social-intel ingestion")
    parser.add_argument("--cycle", action="store_true", help="run one fetch→embed→news_v2 cycle")
    parser.add_argument(
        "--config", action="store_true", help="print config snapshot (key redacted)"
    )
    parser.add_argument("--budget", action="store_true", help="print budget usage snapshot")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if args.config:
        from rugbrain.ingest_sources.x_intel.config import as_dict

        print(json.dumps(as_dict(), indent=2))
        return
    if args.budget:
        from rugbrain.ingest_sources.x_intel.budget import default_tracker

        print(json.dumps(asyncio.run(default_tracker().get_daily_usage()), indent=2))
        return
    if args.cycle:
        sys.exit(_run_cycle())
    parser.print_help()


if __name__ == "__main__":
    main()
