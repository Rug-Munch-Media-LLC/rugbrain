"""One join rule for the rerank endpoint URL.

Four callers used to concatenate ``f"{url}/rerank"`` and each one broke the
same way: a configured URL that already ends in ``/rerank`` (the shape every
hosted provider publishes) became ``.../rerank/rerank``. Joining happens here
so the rule has one owner and no caller has to remember which shape it got.
"""

from __future__ import annotations

_SUFFIX = "/rerank"


def rerank_endpoint(base: str) -> str:
    """The scored-rerank POST URL for *base*, idempotent on the ``/rerank`` suffix."""
    url = (base or "").strip().rstrip("/")
    if not url:
        return ""
    return url if url.endswith(_SUFFIX) else f"{url}{_SUFFIX}"


__all__ = ["rerank_endpoint"]
