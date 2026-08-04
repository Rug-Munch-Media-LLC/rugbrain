"""RugBrain — alias package for backward compatibility.

``import rugbrain`` resolves to the same module object as ``import rmi_rag``,
and ``import rugbrain.alerts`` / ``import rugbrain.market_awareness`` resolve
to the same submodules. This lets new code use the RugBrain name while
existing consumers keep working with ``rmi_rag``.
"""

from __future__ import annotations

import sys

import rmi_rag as _impl

# The sys.modules swap makes `rugbrain` the *same* module as `rmi_rag`, so
# submodule imports (`import rugbrain.alerts`) resolve through rmi_rag's
# __path__ and attribute access behaves identically.
sys.modules[__name__] = _impl
