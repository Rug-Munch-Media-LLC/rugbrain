"""Declared devdocs, group: the AI platform layer (gateway + observability).

Two families, both running on Talos and both routinely mis-configured in exactly the same
way: the doc says a key exists, the released code names it differently, and the config
silently falls back to a default. LiteLLM is the routing gateway (provider fallbacks,
virtual keys, spend caps); Langfuse is the trace/score store the evals read.

BOTH ARE OPEN-CORE. The LICENSE in each repository grants MIT for the tree and carves out
an ``enterprise/`` directory under commercial terms, so the declared token says so rather
than flattening the distinction — a product-bound pass needs the carve-out, and the docs
pages declared here are the core ones.

OVERLAP: the fleet's ``docs:`` stack lane (ContextCrypto) carries its own LiteLLM pages.
That lane lives in a different corpus; this module is the RugBrain-side declaration, kept
narrow (README + the two pages that describe wiring) so the two do not compete in one
top-k cut.

See ``devdocs_schema`` for the field contract and ``devdocs_specs`` for the aggregate.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_OPEN_CORE_EE,
    DocSite,
)

_RAW = "https://raw.githubusercontent.com"

_PLATFORM: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="litellm_docs",
        family="litellm",
        url="https://docs.litellm.ai/",
        licence=LICENCE_OPEN_CORE_EE,
        doc_type="sdk_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/BerriAI/litellm/main/README.md",
            "https://docs.litellm.ai/docs/proxy/configs",
            "https://docs.litellm.ai/docs/completion/input",
        ),
        cap=3,
    ),
    DocSite(
        name="langfuse_docs",
        family="langfuse",
        url="https://langfuse.com/docs",
        licence=LICENCE_OPEN_CORE_EE,
        doc_type="sdk_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/langfuse/langfuse/main/README.md",
            "https://langfuse.com/docs/tracing",
        ),
        cap=2,
    ),
)

#: Gateway + observability docs, in fetch order.
PLATFORM_SITES: Final[tuple[DocSite, ...]] = _PLATFORM

__all__ = ["PLATFORM_SITES"]
