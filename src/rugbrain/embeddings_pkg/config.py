"""Embedding constants and the fail-loud exception.

2026-09-28 (operator decision): the canonical lane is 8B @ 4096-dim, and
that is the CODE DEFAULT, not only the env default. The 4B/2560 generation is
retired. This docstring previously claimed "Qwen3-Embedding-8B (2560d) -
CANONICAL primary", a false statement that survived the migration and misled
every later reader.

Why the default had to move with the env: the env files and all collections
were already 4096, but every code path that lacked the env fell back to 2560
and was then refused by the store's dimension guard:

  qdrant_insert_blocked collection=privacy_protocol requested_dim=2560
                                         configured_dims=[4096]

The guard is correct and stays. The default was the bug. 2048 (nemotron era)
and 2560 (4B) are retired as defaults. A 2560 base collection is deliberately
retained under the no-overwrite rule, so this module can no longer write to
it — intended, because a 4096 vector must never be coerced into a 2560 space.
"""

from __future__ import annotations

import os
from typing import Final

# CANONICAL LANE IS 8B @ 4096. A 2560 base collection is retained under the
# no-overwrite rule, so nothing here can write to it any more.
EMBEDDING_DIM: Final = int(os.getenv("RAG_EMBED_DIM", "4096"))

# Qwen3-Embedding query instruction template. The model was trained with an
# instruction prefix on the QUERY side and plain text on the DOCUMENT side.
# Embedding a raw query without the Instruct template measurably degrades
# retrieval (roughly 1-5% on nDCG/recall). Documents MUST stay raw; only query
# text gets this prefix.
QUERY_INSTRUCT = (
    "Instruct: Given a web search query, retrieve relevant passages that "
    "answer the query\nQuery: {}"
)

# Embedding versioning (ADR-0007 compliance) — stamped on every embedding so
# downstream consumers can migrate when the model changes.
EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
EMBED_MODEL_VERSION = os.getenv("RAG_EMBED_VERSION", "2.0")
EMBED_VERSION_STAMP = f"{EMBED_MODEL_NAME}:{EMBED_MODEL_VERSION}:{EMBEDDING_DIM}"
EMBED_BUILD_ID = os.getenv("RAG_EMBED_BUILD_ID", "")  # git commit hash or CI build

# Backends. "auto" is the two-lane chain; every other value is an explicit
# operator override for a legacy lane.
BACKEND = os.getenv("RAG_EMBED_BACKEND", "auto").lower()

# QWEN_EMBED_URL is the canonical embedder endpoint (llama.cpp Qwen3-8B).
QWEN_EMBED_URL = os.getenv(
    "QWEN_EMBED_URL", os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
).rstrip("/")
GATEWAY_EMBED_URL = os.getenv("GATEWAY_EMBED_URL", "http://127.0.0.1:8010")
QWEN_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b"))
OPENROUTER_URL = os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings")
OPENROUTER_MODEL = os.getenv("RAG_OPENROUTER_EMBED_MODEL", "openrouter/your-model-here")

# ── NVIDIA NIM direct tier (free credits, separate quota from OpenRouter) ──
# Both models output 2048d → drop-in compatible with the shadow `_i`
# collections. Tried in order; 429/5xx rotates to the next model.
NIM_URL = os.getenv("NVIDIA_EMBED_URL", "https://integrate.api.nvidia.com/v1/embeddings").rstrip(
    "/"
)
NIM_KEY = os.getenv("NVIDIA_API_KEY") or os.getenv("NVIDIA_NIM_API_KEY", "")
NIM_MODELS = [
    m.strip()
    for m in os.getenv(
        "NVIDIA_EMBED_MODELS",
        "nvidia/nemotron-3-embed-1b,nvidia/llama-nemotron-embed-vl-1b-v2",
    ).split(",")
    if m.strip()
]

# ── DeepInfra hosted lane (2026-08-29 consolidation) ────────────────────────
# CANONICAL primary: DeepInfra hosts the exact same Qwen3-Embedding-8B the
# local :8004 serves — 4096-dim, same vector space, always warm, no local CPU
# contention. Key: DEEPINFRA_API_KEY env var, else a file at
# DEEPINFRA_KEY_FILE (pattern: the deepinfra_embed embed lane).
DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")


class RAGUnavailableError(Exception):
    """Raised when no embedding backend is reachable.

    The RAG endpoint translates this to HTTP 503 + an explicit empty-result
    body. Policy: fail loud (T04) — no hash fallback, no silent empty
    results. A module that can return an empty embedding with no signal is the
    failure mode this exception exists to prevent.
    """
