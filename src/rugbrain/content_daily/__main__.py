"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

``python -m rugbrain.content_daily`` — delegates to the CLI and nothing else.
"""

from __future__ import annotations

import sys

from rugbrain.content_daily.cli import main

if __name__ == "__main__":
    sys.exit(main())
