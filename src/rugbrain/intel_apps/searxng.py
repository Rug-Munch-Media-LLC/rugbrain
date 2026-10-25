"""searxng: fresh web facts with citations (news, price, release dates)."""
from __future__ import annotations

from typing import Any

from rugbrain.intel_apps.call import DEFAULT_TIMEOUT_S, Invoke, guarded_call
from rugbrain.intel_apps.guard import own_repo_refusal

PROVIDER = "searxng"


async def web_cited_search(
    invoke: Invoke, query: str, *, limit: int = 5, timeout_s: float = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """Extractive answer + citations, or a refusal naming searxng."""
    refusal = own_repo_refusal(query, provider=PROVIDER, tool="cited_search")
    if refusal is not None:
        return refusal
    return await guarded_call(
        provider=PROVIDER,
        tool="cited_search",
        invoke=invoke,
        args={"query": query, "limit": max(1, min(10, limit))},
        timeout_s=timeout_s,
    )
