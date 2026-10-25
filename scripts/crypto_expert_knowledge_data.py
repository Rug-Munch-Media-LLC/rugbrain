"""Deprecated location — data moved to ``rugbrain.knowledge.crypto_expert``.

Thin facade so the existing ``sys.path``-based ingest scripts keep working.
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rugbrain.knowledge.crypto_expert import *  # noqa: F403, E402
from rugbrain.knowledge.crypto_expert import __all__ as __all__  # noqa: E402
