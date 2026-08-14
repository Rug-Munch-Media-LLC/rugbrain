"""rugbrain CLI entry point.

TODO(rugbrain-extract): implement after core modules are ported.
Planned subcommands (mirror private scripts + app/rag service surface):

- ``rugbrain search``  — one-shot hybrid search against a collection
- ``rugbrain ingest``  — ingest a file/URL into a collection
- ``rugbrain crawler`` — run the scheduled crawler once
- ``rugbrain eval``    — run the RAG regression eval gates
- ``rugbrain seed-demo`` — ingest bundled examples/data/*.md (offline demo)
"""

from __future__ import annotations

import argparse
import sys


def _not_ported(name: str) -> int:
    print(f"rugbrain: {name} is not ported yet (scaffold stub, see MIGRATION.md)", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Console entry point (see [project.scripts] in pyproject.toml)."""
    parser = argparse.ArgumentParser(prog="rugbrain", description="Hybrid retrieval RAG engine CLI")
    parser.add_argument(
        "command",
        nargs="?",
        default="search",
        help="search | ingest | crawler | eval | seed-demo",
    )
    args = parser.parse_args(argv)
    return _not_ported(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
