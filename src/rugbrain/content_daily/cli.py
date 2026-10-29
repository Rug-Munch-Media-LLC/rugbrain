"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The operator entry point: one edition, one run, one exit code.

``python -m rugbrain.content_daily daily --edition morning [--dry-run]``

Exit codes are the contract systemd and the on-call rotation read, so they are
coarse on purpose — a timer cannot act on a distinction finer than "nothing
went out, and why not":

====  ======================================================================
0     published (``ok``, ``repaired``) or nothing to do (``skipped``)
1     an unexpected status — a bug, not an editorial outcome
2     refused: ``no_candidates``, ``needs_review``, ``writer_unavailable``,
      ``not_due``
3     ``site_failed`` — the page was not written, so Telegram was not sent
4     ``partial`` — the page is live, the channel post failed
====  ======================================================================

``--json`` prints the run envelope on stdout and nothing else, which is what
the smoke unit and the journal readers consume.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rugbrain.content_daily import editions, fixtures
from rugbrain.content_daily.deps import Deps
from rugbrain.content_daily.pipeline import run_edition

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_REFUSED = 2
EXIT_SITE_FAILED = 3
EXIT_PARTIAL = 4

EXIT_CODES: dict[str, int] = {
    "ok": EXIT_OK,
    "repaired": EXIT_OK,
    "skipped": EXIT_OK,
    "no_candidates": EXIT_REFUSED,
    "needs_review": EXIT_REFUSED,
    "writer_unavailable": EXIT_REFUSED,
    "not_due": EXIT_REFUSED,
    "site_failed": EXIT_SITE_FAILED,
    "partial": EXIT_PARTIAL,
}


def exit_code(result: dict[str, Any]) -> int:
    """The exit code for a finished run envelope."""
    return EXIT_CODES.get(str(result.get("status")), EXIT_UNEXPECTED)


def _day(value: str) -> str:
    """``YYYY-MM-DD`` or an argparse error — a typo must not pick a wrong day."""
    try:
        return datetime.strptime(value, editions.DAY_FORMAT).strftime(editions.DAY_FORMAT)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def parser() -> argparse.ArgumentParser:
    """The CLI surface; one subcommand, because there is one job."""
    top = argparse.ArgumentParser(prog="python -m rugbrain.content_daily")
    sub = top.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="write, publish and deliver one edition")
    daily.add_argument(
        "--edition", required=True, choices=sorted(editions.EDITIONS), help="which edition"
    )
    daily.add_argument("--date", type=_day, default=None, help="UTC day (default: today)")
    daily.add_argument("--dry-run", action="store_true", help="render only; nothing published")
    daily.add_argument(
        "--repair-delivery",
        action="store_true",
        help="re-send Telegram for a run whose site publication already succeeded",
    )
    daily.add_argument(
        "--force", action="store_true", help="run before the edition's slot has arrived"
    )
    daily.add_argument(
        "--offline",
        action="store_true",
        help="deterministic fixture dependencies: no source, no site, no channel",
    )
    daily.add_argument("--json", action="store_true", help="print the run envelope on stdout")
    return top


def _report(result: dict[str, Any], as_json: bool) -> None:
    """One machine-readable line, or a short operator block."""
    if as_json:
        print(json.dumps(result, ensure_ascii=False, default=str))
        return
    print(
        f"{result['status']}: {result['edition']} {result['day']} "
        f"items={result['items']} url={result['url'] or '-'} "
        f"msgs={len(result['message_ids'])}"
    )
    for line in result["errors"]:
        print(f"  error: {line}", file=sys.stderr)
    if result.get("reason"):
        print(f"  reason: {result['reason']}", file=sys.stderr)
    print(result["health"])


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    """Run the edition; ``--offline`` swaps in fixture dependencies."""
    deps: Deps = fixtures.offline_deps() if args.offline else Deps()
    return asyncio.run(
        run_edition(
            args.edition,
            day=args.date,
            dry_run=args.dry_run,
            force=args.force,
            repair_delivery=args.repair_delivery,
            deps=deps,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, run, report, return the exit code."""
    args = parser().parse_args(argv)
    # Lane modules print diagnostics on stdout; --json promises stdout is JSON.
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            result = _execute(args)
    else:
        result = _execute(args)
    _report(result, args.json)
    return exit_code(result)


__all__ = ["EXIT_CODES", "exit_code", "main", "parser"]
