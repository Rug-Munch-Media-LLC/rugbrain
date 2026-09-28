"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The word lists extraction matches against — one import point for data.

A facade over the two lexicons, so callers do not have to know which file
a given word list happens to live in:

  * ``lexicon_severity`` — what counts as critical / high / medium
  * ``lexicon_entities`` — protocol, chain and chain-ticker names

Both are data only; no behaviour lives here. See :mod:`rugbrain.digest.
entity_extract` for the rules applied over them.
"""

from __future__ import annotations

import re
from typing import Final

from rugbrain.digest.lexicon_entities import (
    CHAIN_TICKER_SYMBOLS,
    CHAINS,
    PROTOCOLS,
)
from rugbrain.digest.lexicon_severity import SEVERITY_RULES

# Bare protocol references in prose ("the Euler v2 exploit").
NAME_RE: Final = re.compile(r"(?<![\w-])([a-z][a-z0-9-]{2,20})(?![\w-])", re.IGNORECASE)

__all__ = [
    "CHAINS",
    "CHAIN_TICKER_SYMBOLS",
    "NAME_RE",
    "PROTOCOLS",
    "SEVERITY_RULES",
]
