"""Fleet retrieval layers for RugBrain: exact (zoekt), symbols (serena), compression.

Each adapter returns ``{ok, data, source, error{code, detail}, fallback}`` and
refuses with a typed code instead of degrading to an empty success.
"""

from __future__ import annotations

from rugbrain.fleet_layers.compress import compress
from rugbrain.fleet_layers.envelope import layer_response
from rugbrain.fleet_layers.serena import serena_symbols
from rugbrain.fleet_layers.zoekt import zoekt_search

__all__ = ["compress", "layer_response", "serena_symbols", "zoekt_search"]
