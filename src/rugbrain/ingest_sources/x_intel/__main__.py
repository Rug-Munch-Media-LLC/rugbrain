"""CLI for ``python -m rugbrain.ingest_sources.x_intel``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging


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
        from rugbrain.ingest_sources.x_intel.pipeline import run_cycle

        print(json.dumps(asyncio.run(run_cycle()), indent=2))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
