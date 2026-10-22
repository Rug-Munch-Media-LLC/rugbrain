#!/usr/bin/env python3
"""Build data/x_sources.json from the curated seed + existing handle inventory."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xsources.build import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
