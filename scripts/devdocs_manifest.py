#!/usr/bin/env python3
"""Render the devdocs manifest: every declared source, its licence and its canonical URL.

A VIEW, never a second table. :mod:`rugbrain.ingest_sources.devdocs_specs` is the single
source of truth; this script only projects it, so a rendered manifest cannot disagree with
what the fetcher actually pulls. Same pattern as ``scripts/corpus_manifest.py`` reading
``rugbrain.corpus.entries``.

Usage:
    python scripts/devdocs_manifest.py                  # markdown table to stdout
    python scripts/devdocs_manifest.py --json           # JSON document
    python scripts/devdocs_manifest.py --json --out docs/SOURCES-DEVDOCS.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rugbrain.ingest_sources.devdocs_specs import DOC_SITES, DocSite

MARKDOWN_HEADER = (
    "| source | family | doc_type | collection | licence | pages | canonical url |\n"
    "| --- | --- | --- | --- | --- | ---: | --- |"
)


def site_row(site: DocSite) -> dict[str, Any]:
    """One declared site as a plain record (the JSON shape and the table source)."""
    return {
        "name": site.name,
        "family": site.family,
        "doc_type": site.doc_type,
        "collection": site.collection,
        "licence": site.licence,
        "cap": site.cap,
        "url": site.url,
        "urls": list(site.urls),
    }


def render_markdown(sites: tuple[DocSite, ...]) -> str:
    """Project the declaration set to a provenance table."""
    lines = [MARKDOWN_HEADER]
    for site in sites:
        lines.append(
            f"| `{site.name}` | {site.family} | {site.doc_type} | `{site.collection}` | "
            f"{site.licence} | {len(site.urls)} | {site.url} |"
        )
    total = sum(len(s.urls) for s in sites)
    lines.append("")
    lines.append(f"{len(sites)} declared sources, {total} declared pages.")
    return "\n".join(lines) + "\n"


def render_json(sites: tuple[DocSite, ...]) -> str:
    """Project the declaration set to a machine-readable document."""
    payload = {
        "manifest": "rugbrain.devdocs",
        "version": 1,
        "source_of_truth": "src/rugbrain/ingest_sources/devdocs_specs.py",
        "declared_sources": len(sites),
        "declared_pages": sum(len(s.urls) for s in sites),
        "sites": [site_row(site) for site in sites],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--out", default="", help="write to this path instead of stdout")
    args = parser.parse_args()

    rendered = render_json(DOC_SITES) if args.json else render_markdown(DOC_SITES)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        print(f"wrote {args.out} ({len(DOC_SITES)} sources)", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
