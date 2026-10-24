#!/usr/bin/env python3
"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Publish one Daily Rug long-form and print the JSON envelope.

Exit 0 when published, 1 when the evidence gate refused it, 2 on usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.daily_rug_publish import publish_daily_rug


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish one Daily Rug brief")
    parser.add_argument("topic", help="story topic, e.g. bridge key compromise drain")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today (UTC)")
    args = parser.parse_args()
    result = asyncio.run(publish_daily_rug(args.topic, date=args.date))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("published") else 1


if __name__ == "__main__":
    raise SystemExit(main())
