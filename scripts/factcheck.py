#!/usr/bin/env python3
"""CLI for the RugBrain fact-check gate (draft gate only).

Reads a draft (file or stdin) and a JSON source list, prints the per-claim
verdict report as JSON. Exit code is 0 when the draft passes, 1 when it is
flagged — so it can gate a publish step later.

Usage::

    python scripts/factcheck.py --draft draft.txt --sources sources.json
    cat draft.txt | python scripts/factcheck.py --sources sources.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.factcheck import check_draft


def _load_draft(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def _load_sources(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sources", [])
    if not isinstance(data, list):
        raise SystemExit("sources file must be a JSON list or {'sources': [...]}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="RugBrain fact-check gate")
    parser.add_argument("--draft", default=None, help="draft text file (default: stdin)")
    parser.add_argument("--sources", required=True, help="JSON source list")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    args = parser.parse_args()

    report = check_draft(_load_draft(args.draft), _load_sources(args.sources))
    print(json.dumps(report, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
