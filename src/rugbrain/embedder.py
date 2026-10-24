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

import os

import httpx

from rugbrain.dim_policy import default_dim_for_model
from rugbrain.embedder_config import _PROVIDERS, MODEL, logger
from rugbrain.embedder_providers import _call, _key, _order


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
