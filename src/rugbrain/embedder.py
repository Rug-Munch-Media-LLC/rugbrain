"""RAG Embedder — Qwen3-Embedding-8B hosted fallback chain.

Provider chain is chosen via ``EMBED_PROVIDER_ORDER`` (comma-separated):
  deepinfra, siliconflow, openrouter

All three serve **Qwen/Qwen3-Embedding-8B** (4096-dim). Each rung returns
``None`` on any failure (missing key, non-200, malformed body, wrong dim) so
the chain falls through. If every rung fails, :class:`RAGUnavailableError`
is raised.

The retired local llama.cpp lane (``:8004``) is removed. If an operator
explicitly requests ``EMBED_PROVIDER=int`` or ``local`` a ``RuntimeError``
is raised — fail loud per T04.

Dim policy is enforced by :mod:`rugbrain.dim_policy` (rejects truncation,
rejects padding). All providers must return 4096-dim vectors.
"""

from __future__ import annotations

import logging
import os

import httpx

from rugbrain.dim_policy import default_dim_for_model, normalize_dims
from rugbrain.embeddings_pkg.config import RAGUnavailableError

logger = logging.getLogger(__name__)

MODEL = os.getenv("EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-8B")
TIMEOUT = float(os.getenv("EMBED_HOSTED_TIMEOUT", "30.0"))
DEFAULT_ORDER = ("deepinfra", "siliconflow", "openrouter")

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
        "/srv/secrets/openrouter_account1_api_key",
    ),
}

_KEYS: dict[str, str] = {}


def _key(provider: str) -> str:
    """Credential for a provider: env first, then the spooled key file."""
    if provider in _KEYS:
        return _KEYS[provider]
    _, _, env_name, key_file = _PROVIDERS[provider]
    value = os.getenv(env_name, "").strip()
    if not value:
        try:
            with open(key_file, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            value = ""
    _KEYS[provider] = value
    return value


def _order() -> tuple[str, ...]:
    raw = os.getenv("EMBED_PROVIDER_ORDER", "")
    names = [n.strip().lower() for n in raw.split(",") if n.strip().lower() in _PROVIDERS]
    return tuple(names) + tuple(p for p in DEFAULT_ORDER if p not in names)


async def _call(
    provider: str, texts: list[str], timeout: float | None  # noqa: ASYNC109 — forwarded to HTTP client
) -> list[list[float]] | None:
    """Call a single provider for embeddings. Returns list of vectors or None."""
    base, model, _, _ = _PROVIDERS[provider]
    key = _key(provider)
    if not key:
        logger.debug("embed_%s_skipped no_api_key", provider)
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            resp = await client.post(
                base,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": texts},
            )
        if resp.status_code != 200:
            logger.warning("embed_%s_status=%d", provider, resp.status_code)
            return None
        items = (resp.json().get("data") or [])
        if len(items) != len(texts):
            logger.warning("embed_%s_count got=%d want=%d", provider, len(items), len(texts))
            return None
        vectors = []
        for entry in sorted(items, key=lambda i: i.get("index", 0)):
            vec = entry.get("embedding")
            if not isinstance(vec, list):
                logger.warning("embed_%s_shape bad_entry=%s", provider, str(entry)[:80])
                return None
            vectors.append(vec)
    except Exception as exc:  # a rung must never kill the chain
        logger.debug("embed_%s_failed: %s", provider, str(exc)[:120])
        return None

    # Normalize dimensions (rejects wrong dims per dim_policy)
    normalized = []
    for i, vec in enumerate(vectors):
        try:
            normalized.append(normalize_dims(vec, 4096))
        except ValueError as exc:
            logger.warning("embed_%s_dim_mismatch idx=%d: %s", provider, i, exc)
            return None
    return normalized


async def hosted_embed(
    texts: list[str],
    *,
    timeout: float | None = None,  # noqa: ASYNC109 — forwarded to the HTTP client
) -> list[list[float]] | None:
    """Embed *texts* on the first healthy hosted provider.

    Returns list of 4096-dim vectors, or ``None`` when every rung is
    unavailable — the same contract the single-provider lane had.
    """
    if not texts:
        return None
    for provider in _order():
        if not _key(provider):
            continue
        try:
            vectors = await _call(provider, texts, timeout)
        except httpx.HTTPError as exc:
            logger.warning("embed_rung_failed", extra={"provider": provider, "err": str(exc)[:120]})
            continue
        if not vectors:
            continue
        if any(len(v) != default_dim_for_model(MODEL) for v in vectors):
            logger.warning(
                "embed_rung_wrong_dim",
                extra={"provider": provider, "dim": len(vectors[0])},
            )
            continue
        logger.info("embed_lane", extra={"provider": provider, "texts": len(texts)})
        return vectors
    return None


class HostedEmbedder:
    """Hosted embedding chain adapter (same interface as old DeepInfraEmbedder)."""

    def __init__(self) -> None:
        from rugbrain.dim_policy import default_dim_for_model

        self.dim = default_dim_for_model(MODEL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result = await hosted_embed(texts)
        if result is None:
            from rugbrain.embeddings_pkg.config import RAGUnavailableError

            raise RAGUnavailableError(
                f"all hosted embedding providers exhausted (chain={_order()})"
            )
        return result

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0] if result else [0.0] * self.dim


def get_embedder() -> HostedEmbedder:
    """Return the canonical hosted embedder (qwen3-embedding-8b, 4096d).

    Provider chain selected by ``EMBED_PROVIDER_ORDER``:
      * unset → default order: DeepInfra → SiliconFlow → OpenRouter
      * comma-separated list of providers (subset of the three above)

    The old ``EMBED_PROVIDER`` values ``int`` and ``local`` are no longer
    supported. If set to ``int`` or ``local`` a ``RuntimeError`` is raised —
    fail loud.

    Async interface: ``await embedder.embed([texts])`` or ``await embedder.embed_one(text)``.
    """
    provider = os.getenv("EMBED_PROVIDER", "").strip().lower()
    if provider in ("int", "local"):
        raise RuntimeError(
            "EMBED_PROVIDER=int|local is not supported — the local llama.cpp lane is retired. "
            "Use EMBED_PROVIDER_ORDER to configure the hosted chain."
        )
    return HostedEmbedder()


# ── CLI ────────────────────────────────────────────────────────────────
def _main() -> None:
    """CLI: list available providers and their dims."""
    import argparse

    parser = argparse.ArgumentParser(description="RAG hosted embedding chain")
    parser.add_argument("--list-providers", action="store_true", help="List available providers")
    args = parser.parse_args()

    if args.list_providers:
        from rugbrain.dim_policy import default_dim_for_model

        for prov, (_, model, _, _) in _PROVIDERS.items():
            print(f"{prov}  {model}  {default_dim_for_model(model)}d")


if __name__ == "__main__":
    _main()
