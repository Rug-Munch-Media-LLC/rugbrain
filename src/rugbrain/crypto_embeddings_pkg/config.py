"""Configuration for the crypto embedder package.

All values are read from the environment at import time, exactly as the
former ``crypto_embeddings.py`` module did.
"""

from __future__ import annotations

import os

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PW = os.getenv("REDIS_PASSWORD", "")

# Embedding dimensions by model.
# Configure via RAG_EMBED_DIMS env var: "model1:dim1,model2:dim2"
# Example: "your-model:1024,fallback:384"
_DIMS_ENV = os.getenv("RAG_EMBED_DIMS", "")
DIMS: dict[str, int] = {}
if _DIMS_ENV:
    for entry in _DIMS_ENV.split(","):
        if ":" in entry:
            model, dim = entry.split(":", 1)
            DIMS[model.strip()] = int(dim.strip())
if not DIMS:
    # Sensible defaults showing the pattern
    DIMS = {
        "qwen3-embedding-4b": 2560,
        "fallback-model": 2560,
        "behavioral": 64,
        "code_hash": 128,
    }

# Default model per head.
# Configure via RAG_HEAD_DEFAULTS env var: "head:model,head:model"
# Example: "semantic:your-model,code:your-model"
_HEAD_ENV = os.getenv("RAG_HEAD_DEFAULTS", "")
HEAD_DEFAULTS: dict[str, str] = {}
if _HEAD_ENV:
    for entry in _HEAD_ENV.split(","):
        if ":" in entry:
            head, model = entry.split(":", 1)
            HEAD_DEFAULTS[head.strip()] = model.strip()
if not HEAD_DEFAULTS:
    HEAD_DEFAULTS = {
        "semantic": "your-embedding-model",
        "code": "your-embedding-model",
        "behavioral": "behavioral-embedder",
        "entity": "behavioral-embedder",
    }

# Primary embedding model (set your model name here or via env var)
OR_PRIMARY_EMBED = os.getenv("RAG_PRIMARY_EMBED_MODEL", "openrouter/your-model-here")
# OpenRouter fallback model
OR_PAID_FALLBACK = os.getenv("RAG_FALLBACK_EMBED_MODEL", "openrouter/your-fallback-here")
# Always-available local fallback
OR_FALLBACK_EMBED = os.getenv("RAG_LOCAL_EMBED_MODEL", "local/your-local-model")
# Embed API endpoint (set your preferred provider URL)
NVIDIA_EMBED_URL = os.getenv(
    "RAG_EMBED_URL", "https://your-embed-provider.example.com/v1/embeddings"
)

# Collection names for vector storage.
# Configure via RAG_COLLECTIONS env var (comma-separated).
# Example: RAG_COLLECTIONS="profiles,analysis,patterns"
_COLLECTIONS_ENV = os.getenv("RAG_COLLECTIONS", "")
if _COLLECTIONS_ENV:
    COLLECTIONS = [c.strip() for c in _COLLECTIONS_ENV.split(",")]
else:
    COLLECTIONS = [
        "threat_intel_v2",
        "rmi_agent_memory",
        "rmi_community",
    ]
