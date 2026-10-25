"""RAG Embedder — Qwen3-Embedding-8B hosted fallback chain.

Provider chain is chosen via ``EMBED_PROVIDER_ORDER`` (comma-separated):
  deepinfra, siliconflow, openrouter

All three serve **Qwen/Qwen3-Embedding-8B** (4096-dim). Each rung returns
``None`` on any failure (missing key, non-200, malformed body, wrong dim) so
the chain falls through. If every rung fails, :class:`RAGUnavailableError`
is raised.

The local llama.cpp lane (``:8024``, ``qwen3-embedding-8b``, 4096-d) is the
last rung of the default chain: it is slow (CPU, one slot) but always
available, batched and given a 180 s per-request timeout. ``EMBED_PROVIDER=local``
forces it first.

Dim policy is enforced by :mod:`rugbrain.dim_policy` (rejects truncation,
rejects padding). All providers must return 4096-dim vectors.
"""

from __future__ import annotations

import os

import httpx

from rugbrain.dim_policy import default_dim_for_model
from rugbrain.embedder_config import _PROVIDERS, DEFAULT_ORDER, MODEL, logger
from rugbrain.embedder_providers import _call, _key, _order
from rugbrain.embedder_providers.hosted import LAST_FAILURE
from rugbrain.embeddings_pkg.config import RAGUnavailableError

__all__ = [
    "DEFAULT_ORDER",
    "LAST_FAILURES",
    "HostedEmbedder",
    "RAGUnavailableError",
    "get_embedder",
    "hosted_embed",
]

#: rung -> reason from the most recent exhausted chain (empty on success)
LAST_FAILURES: list[str] = []


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
    failures: list[str] = []
    for provider in _order():
        if not _key(provider):
            failures.append(f"{provider}=no_key")
            continue
        try:
            vectors = await _call(provider, texts, timeout)
        except httpx.HTTPError as exc:
            logger.warning("embed_rung_failed", extra={"provider": provider, "err": str(exc)[:120]})
            failures.append(f"{provider}=http:{type(exc).__name__}")
            continue
        if not vectors:
            failures.append(f"{provider}={LAST_FAILURE.get(provider, 'no_vectors')}")
            continue
        if any(len(v) != default_dim_for_model(MODEL) for v in vectors):
            logger.warning(
                "embed_rung_wrong_dim",
                extra={"provider": provider, "dim": len(vectors[0])},
            )
            failures.append(f"{provider}=wrong_dim")
            continue
        logger.info("embed_lane", extra={"provider": provider, "texts": len(texts)})
        LAST_FAILURES.clear()
        return vectors
    LAST_FAILURES.clear()
    LAST_FAILURES.extend(failures)
    return None


class HostedEmbedder:
    """Hosted embedding chain adapter (same interface as old DeepInfraEmbedder)."""

    def __init__(self) -> None:
        self.dim = default_dim_for_model(MODEL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result = await hosted_embed(texts)
        if result is None:
            raise RAGUnavailableError(
                "all embedding rungs exhausted: "
                + "; ".join(LAST_FAILURES or [f"{p}=unknown" for p in _order()])
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
        # Local llama.cpp lane (:8024, 4096-d) is a first-class rung now.
        os.environ.setdefault("EMBED_PROVIDER_ORDER", "local")
        logger.info("embed_provider_local_forced")
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
