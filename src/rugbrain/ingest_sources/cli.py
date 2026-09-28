"""``python -m rugbrain.ingest_sources`` — the operator CLI.

One job — argument parsing and the operator line per source. A failed fetch is
a non-zero exit: a green run must mean documents were fetched, not that the
HTTP layer quietly returned nothing.

Usage::

    python -m rugbrain.ingest_sources --source swc_registry --limit 3
    python -m rugbrain.ingest_sources --list
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from rugbrain.ingest_sources import registry
from rugbrain.ingest_sources.fetch import fetch_source_result
from rugbrain.ingest_sources.outcome import SourceResult


def print_result(result: SourceResult) -> None:
    """One operator line: count plus the reason when the source is dead."""
    if result.ok:
        print(f"{result.name}: fetched {result.count} docs")
        return
    print(
        f"{result.name}: fetched {result.count} docs — FAILED {result.error.as_log_line()}",
        file=sys.stderr,
    )


def _list_sources() -> int:
    for s in registry.SOURCES:
        print(f"{s.name:<26} {s.doc_type:<22} {s.description}")
    return 0


def _fetch_one(name: str, limit: int | None) -> int:
    if registry.get_source(name) is None:
        print(f"unknown source: {name}; available: {registry.list_sources()}")
        return 2
    result = asyncio.run(fetch_source_result(name, limit))
    print_result(result)
    for d in result.docs[:5]:
        print(f"  - {d.title or d.doc_id} | {d.url}")
    # A failed fetch is a non-zero exit: a green run must mean documents
    # were fetched, not that the HTTP layer quietly returned nothing.
    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="RugBrain programmatic data sources")
    parser.add_argument("--list", action="store_true", help="list sources + doc_types")
    parser.add_argument("--source", type=str, default=None, help="fetch one source by name")
    parser.add_argument("--limit", type=int, default=None, help="max docs per source")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if args.list:
        return _list_sources()
    if args.source:
        return _fetch_one(args.source, args.limit)
    parser.print_help()
    return 0


def _main() -> int:
    """Console entry point: run the CLI and propagate its exit code."""
    return main()


if __name__ == "__main__":
    sys.exit(main())
