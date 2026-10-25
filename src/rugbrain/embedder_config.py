"""Hosted embedding chain configuration: providers, credentials, timeouts."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MODEL = os.getenv("EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-8B")
TIMEOUT = float(os.getenv("EMBED_HOSTED_TIMEOUT", "30.0"))
#: local (:8024) is last — it always works but is ~100x slower than hosted.
DEFAULT_ORDER = ("siliconflow", "openrouter", "openrouter2", "deepinfra", "local")
LOCAL_TIMEOUT = float(os.getenv("EMBED_LOCAL_TIMEOUT", "180"))

#: provider -> (base url, model id, credential env var, credential file)
_PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "deepinfra": (
        "https://api.deepinfra.com/v1/embeddings",
        "Qwen/Qwen3-Embedding-8B",
        "DEEPINFRA_API_KEY",
        "/srv/secrets/deepinfra_api_key",
    ),
    "siliconflow": (
        "https://api.siliconflow.com/v1/embeddings",
        "Qwen/Qwen3-Embedding-8B",
        "SILICONFLOW_API_KEY",
        "/srv/secrets/siliconflow_api_key",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1/embeddings",
        "qwen/qwen3-embedding-8b",
        "OPENROUTER_API_KEY",
        "/srv/secrets/openrouter_account2_api_key",
    ),
    "local": (
        "http://127.0.0.1:8024/v1/embeddings",
        "qwen3-embedding-8b",
        "LLAMA_EMBED_KEY",
        "/srv/models/embed-lane.env",
    ),
    "openrouter2": (
        "https://openrouter.ai/api/v1/embeddings",
        "qwen/qwen3-embedding-8b",
        "OPENROUTER_BACKUP_API_KEY",
        "/srv/secrets/openrouter_account2_api_key",
    ),
}

_KEYS: dict[str, str] = {}
