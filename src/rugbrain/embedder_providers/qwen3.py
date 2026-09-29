"""Qwen3-Embedding-4B provider — DeepInfra hosted primary, llama.cpp local fallback.

Both lanes serve the SAME Qwen3-Embedding-4B model, so their 2560-dim
vectors are interchangeable per collection (``rugbrain.dim_policy.MODEL_DIMS``).
DeepInfra is the massive-scale lane: benchmarked 2026-08-29 at ~23 ms/doc
vs ~1947 ms/doc for local llama.cpp. Key comes from env
(``DEEPINFRA_API_KEY``, supplied via .env.rag / gopass) — never hardcoded.
"""

from __future__ import annotations

import logging
import os

from rugbrain.dim_policy import default_dim_for_model
from rugbrain.embedder_providers.qwen3_transport import (
    post_deepinfra as _post_deepinfra,
)
from rugbrain.embedder_providers.qwen3_transport import (
    post_llamacpp as _post_llamacpp,
)

logger = logging.getLogger(__name__)


def _deepinfra_key_from_file() -> str:
    """Key-file fallback: explicit env path, host path, container mount."""
    candidates = []
    explicit = os.getenv("DEEPINFRA_KEY_FILE", "").strip()
    if explicit:
        candidates.append(explicit)
    candidates += ["/srv/secrets/deepinfra_api_key", "/run/secrets/deepinfra_api_key"]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
        except OSError:
            continue
    return ""


# Env wins; the file fallback keeps auto mode working when the shell/env
# did not source the secrets file (2026-09-14: /run/secrets path was stale).
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "").strip() or _deepinfra_key_from_file()
DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1").rstrip("/")
DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")


def _embed_url() -> str:
    """The full DeepInfra embeddings endpoint, suffix appended exactly once.

    Tolerates an env value that already includes ``/embeddings``: appending a
    second one yields ``/v1/embeddings/embeddings``, which DeepInfra answers
    with a bare 404 that names neither the cause nor the fix.
    """
    base = (os.getenv("DEEPINFRA_EMBED_URL") or DEEPINFRA_EMBED_URL).rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


class Qwen3Embedder:
    """Qwen3-Embedding-4B provider — DeepInfra hosted or local llama.cpp.

    Provider selection (``EMBED_PROVIDER`` env):
      * ``deepinfra`` — hosted DeepInfra ``Qwen/Qwen3-Embedding-4B``
        (2560-dim). The massive-scale lane (~80x faster than local llama.cpp).
        Requires ``DEEPINFRA_API_KEY``; raises at construction if missing.
      * ``local``     — llama.cpp ``/embeddings`` (``QWEN_EMBED_URL``,
        default http://127.0.0.1:8004/v1). Canonical same-model fallback.
      * unset         — auto: DeepInfra when ``DEEPINFRA_API_KEY`` is set,
        otherwise local llama.cpp.

    An explicit ``embed_url`` pins the local lane (no cloud fallback).
    """

    EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
    MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
    DIMS = int(os.getenv("QWEN_EMBED_DIMS") or default_dim_for_model(MODEL))

    def __init__(self, embed_url: str | None = None, provider: str | None = None):
        self.url = embed_url or os.getenv("QWEN_EMBED_URL", self.EMBED_URL)
        self.provider = (provider or os.getenv("EMBED_PROVIDER") or "").strip().lower()
        self.api_key = DEEPINFRA_API_KEY
        if embed_url is not None:
            # Explicit endpoint pinned (legacy speed tiers) — local lane only.
            self.provider = "local"
        if not self.provider:
            # Auto: hosted DeepInfra when keyed, else local llama.cpp fallback.
            self.provider = "deepinfra" if self.api_key else "local"
        if self.provider == "deepinfra" and not self.api_key:
            raise RuntimeError(
                "EMBED_PROVIDER=deepinfra requires DEEPINFRA_API_KEY "
                "(env, via .env.rag / gopass). Set EMBED_PROVIDER=local for llama.cpp."
            )
        if self.provider not in ("deepinfra", "local"):
            raise RuntimeError(f"EMBED_PROVIDER={self.provider!r} unknown (use deepinfra|local)")

    @property
    def dims(self) -> int:
        """Expected vector dim, resolved at CALL time.

        The class attribute ``DIMS`` is frozen at import from whatever model
        was configured then. An operator switching the model afterwards (the
        8B lane is 4096-dim, the corpus is 2560-dim) would otherwise be
        validated against the previous model's dim, which either truncates
        silently or rejects good vectors.
        """
        override = os.getenv("QWEN_EMBED_DIMS")
        if override:
            return int(override)
        return default_dim_for_model(self.MODEL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "deepinfra":
            return await self._embed_deepinfra(texts)
        return await self._embed_llamacpp(texts)

    async def _embed_deepinfra(self, texts: list[str]) -> list[list[float]]:
        """Hosted DeepInfra lane — batched OpenAI-compatible /embeddings.

        The URL and model are re-read from the environment on every call, not
        captured at import time. Two reasons, both learned the hard way:

        * A ``DEEPINFRA_EMBED_URL`` that already ends in ``/embeddings`` made
          the client post to ``.../v1/embeddings/embeddings`` and every call
          404'd. Normalising the suffix here fixes that shape for every caller
          instead of requiring each one to pre-strip it.
        * Module-level constants are frozen at import, so a caller that fixes
          the env after importing the package was silently ignored.
        """
        return await _post_deepinfra(self, texts)

    async def _embed_llamacpp(self, texts: list[str]) -> list[list[float]]:
        return await _post_llamacpp(self, texts)

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]
