"""Embedding backend lanes — the HTTP half of :mod:`rugbrain.embeddings`.

Split from the 538-line ``embeddings.py`` so each lane is one job:

* :mod:`.config` — the constants, the endpoint URLs, the fail-loud exception.
* :mod:`.normalize` — L2 normalise, MRL truncate, and the dim-mismatch REJECT.
* :mod:`.local_lane` — llama.cpp ``:8004``.
* :mod:`.hosted_lane` — DeepInfra (canonical primary).
* :mod:`.legacy_lanes` — gateway / OpenRouter / NIM, dropped from ``auto``.

``embeddings.py`` keeps the public API and re-exports every name the test
suite monkeypatches, so a lane that is patched in one place cannot drift from
the chain that calls it.
"""

from __future__ import annotations

from rugbrain.embeddings_pkg.config import (
    EMBED_BUILD_ID,
    EMBED_MODEL_NAME,
    EMBED_MODEL_VERSION,
    EMBED_VERSION_STAMP,
    EMBEDDING_DIM,
    QUERY_INSTRUCT,
    RAGUnavailableError,
)
from rugbrain.embeddings_pkg.hosted_lane import embed as deepinfra_embed
from rugbrain.embeddings_pkg.hosted_lane import ready as deepinfra_ready
from rugbrain.embeddings_pkg.legacy_lanes import (
    gateway_embed,
    nim_embed,
    openrouter_embed,
    or_key_pool,
)
from rugbrain.embeddings_pkg.local_lane import embed as int_embed
from rugbrain.embeddings_pkg.local_lane import reachable as int_reachable
from rugbrain.embeddings_pkg.normalize import resize, truncate_mrl

__all__ = [
    "EMBEDDING_DIM",
    "EMBED_BUILD_ID",
    "EMBED_MODEL_NAME",
    "EMBED_MODEL_VERSION",
    "EMBED_VERSION_STAMP",
    "QUERY_INSTRUCT",
    "RAGUnavailableError",
    "deepinfra_embed",
    "deepinfra_ready",
    "gateway_embed",
    "int_embed",
    "int_reachable",
    "nim_embed",
    "openrouter_embed",
    "or_key_pool",
    "resize",
    "truncate_mrl",
]
