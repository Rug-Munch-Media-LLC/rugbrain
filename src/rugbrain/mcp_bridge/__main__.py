"""Allow ``python -m rugbrain.mcp_bridge <tool> <query>``."""

from __future__ import annotations

import asyncio
import sys

from rugbrain.mcp_bridge.cli import _cli_main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli_main(sys.argv)))
