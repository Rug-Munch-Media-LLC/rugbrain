#!/usr/bin/env python3
"""Load the curated GambleFi / crypto-gaming source list from TSV.

The list is DATA, not code: ``scripts/gamblefi_sources.tsv`` is reviewable as a
one-row-per-URL diff, and this module is the single place that turns a row into
the tuple the fetcher consumes. Keeping the rows out of the module is also what
keeps the module under the repo's 200-line cap once a legitimate 200-source
crawl arrives.

Row shape (tab separated, ``#`` starts a comment)::

    tier  url  publisher  doc_type  licence  title

``tier`` is ``official`` (the operator's own documentation, whitepaper,
provably-fair page or repository) or ``community``. ``url`` beginning
``gh:`` is a repository path and resolves to
``raw.githubusercontent.com/<owner>/<repo>/main/<path>``; that is the only
network indirection here, so an unreadable row fails loudly instead of being
guessed at.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

GH: Final = "https://raw.githubusercontent.com"
DEFAULT_SOURCES: Final = Path(__file__).with_name("gamblefi_sources.tsv")
FIELDS: Final = 6
TIERS: Final = frozenset({"official", "community"})


def _url(raw: str) -> str:
    """Expand ``gh:<owner>/<repo>/<path>`` to a raw file URL; else return as-is."""
    if not raw.startswith("gh:"):
        return raw
    owner, repo, path = raw[3:].lstrip("/").split("/", 2)
    branch = "" if path.startswith("main/") else "main/"
    return f"{GH}/{owner}/{repo}/{branch}{path}"


def load(path: Path = DEFAULT_SOURCES) -> list[tuple[str, str, str, str, str, str]]:
    """Parse the TSV into ``(url, title, publisher, licence, doc_type, tier)``."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) != FIELDS:
            raise ValueError(f"{path}:{lineno}: want {FIELDS} columns, got {len(parts)}")
        tier, url, publisher, doc_type, licence, title = parts
        if tier not in TIERS:
            raise ValueError(f"{path}:{lineno}: tier {tier!r} not in {sorted(TIERS)}")
        rows.append((_url(url), title, publisher, licence, doc_type, tier))
    return rows


SOURCES: Final = load()
SEEDS: Final = tuple(r for r in SOURCES if not r[0].startswith(GH))
REPOS: Final = tuple(r for r in SOURCES if r[0].startswith(GH))

__all__ = ["DEFAULT_SOURCES", "GH", "REPOS", "SEEDS", "SOURCES", "load"]
