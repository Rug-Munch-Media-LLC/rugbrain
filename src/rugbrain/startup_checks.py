"""Fail-fast startup validation: a retired lane must not reach a request path."""

from __future__ import annotations

import os

#: Values that name the retired local llama.cpp embed lane.
RETIRED_EMBED_PROVIDERS = ("int", "local")


def assert_runtime_config() -> None:
    """Refuse to start when a retired embed lane is configured.

    ``get_embedder()`` raises mid-request for these values; failing at startup
    turns a 500 into a clear, immediate deploy-time error.
    """
    provider = os.getenv("EMBED_PROVIDER", "").strip().lower()
    if provider in RETIRED_EMBED_PROVIDERS:
        raise RuntimeError(
            f"EMBED_PROVIDER={provider!r} is retired (local llama.cpp lane removed). "
            "Unset it or set EMBED_PROVIDER=deepinfra, and choose the hosted chain with "
            "EMBED_PROVIDER_ORDER (deepinfra,siliconflow,openrouter). Refusing to start."
        )


__all__ = ["RETIRED_EMBED_PROVIDERS", "assert_runtime_config"]
