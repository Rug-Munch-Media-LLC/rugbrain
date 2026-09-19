"""CLI entry point for the security-alert poller."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

from .loop import run_alert_poller
from .poller import poll_once


def main() -> None:
    parser = argparse.ArgumentParser(description="RugBrain security alert poller")
    parser.add_argument("--once", action="store_true", help="Run a single poll pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="Detect without embedding/storing")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("RAG_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.once:
        results = asyncio.run(poll_once(dry_run=args.dry_run))
        print(json.dumps(results, indent=2, default=str))
    else:
        asyncio.run(run_alert_poller(dry_run=args.dry_run))
