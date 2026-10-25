"""grepapp: real-world OSS usage of an API (literal/regex code search)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rugbrain.intel_apps.call import DEFAULT_TIMEOUT_S, Invoke, guarded_call
from rugbrain.intel_apps.guard import own_repo_refusal

PROVIDER = "grepapp"


async def grep_github(
    invoke: Invoke,
    query: str,
    *,
    language: Sequence[str] | None = None,
    repo: str | None = None,
    path: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Search public GitHub code for a literal pattern like ``httpx.AsyncClient(``."""
    refusal = own_repo_refusal(repo or "", provider=PROVIDER, tool="searchGitHub")
    if refusal is not None:
        return refusal
    args: dict[str, Any] = {"query": query}
    if language:
        args["language"] = list(language)
    if repo:
        args["repo"] = repo
    if path:
        args["path"] = path
    return await guarded_call(
        provider=PROVIDER,
        tool="searchGitHub",
        invoke=invoke,
        args=args,
        timeout_s=timeout_s,
    )
