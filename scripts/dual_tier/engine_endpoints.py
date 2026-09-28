"""Base URLs of the local model engine servers, keyed by capability.

These are the *serving* endpoints a gateway proxies to — an HTTP POST to a
separate engine process. They are deliberately NOT the retriever's concern:
``rugbrain.search.dual_tier`` embeds through the canonical in-process seam
(``embed_query`` -> ``get_query_embedding``) and owns no URL at all. The old
arrangement — an ``ENDPOINTS`` map inside ``dual_tier`` plus a private HTTP
embed client pointed at ``:8004`` — is what made two modules disagree about
which embedder was live, and the dead one silently returned zero documents.

One job: name the engine servers. No HTTP calls, no breaker, no tier routing.

The 0.6B "speed" tier was retired 2026-09-14, so ``speed_*`` resolves to the
canonical 4B lanes. An explicit ``SPEED_*`` env override still wins if a speed
server is ever revived; until then both labels are the same process.
"""

from __future__ import annotations

import os

_DEEP_EMBED = os.getenv("DEEP_EMBED_URL", "http://127.0.0.1:8004")
_DEEP_RERANK = os.getenv("DEEP_RERANK_URL", "http://127.0.0.1:8003")

#: capability key -> base URL of the engine that serves it.
ENDPOINTS: dict[str, str] = {
    "chat": os.getenv("CHAT_URL", "http://127.0.0.1:8009"),
    "deep_embed": _DEEP_EMBED,
    "deep_rerank": _DEEP_RERANK,
    "speed_embed": os.getenv("SPEED_EMBED_URL", _DEEP_EMBED),
    "speed_rerank": os.getenv("SPEED_RERANK_URL", _DEEP_RERANK),
}

__all__ = ["ENDPOINTS"]
