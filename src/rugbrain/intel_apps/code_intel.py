"""code-intel: maintenance + supply-chain facts for third-party code."""
from __future__ import annotations

from typing import Any

from rugbrain.intel_apps.call import DEFAULT_TIMEOUT_S, Invoke, guarded_call
from rugbrain.intel_apps.guard import own_repo_refusal

PROVIDER = "code-intel"


async def code_intel_facts(
    invoke: Invoke,
    *,
    owner: str | None = None,
    repo: str | None = None,
    system: str | None = None,
    name: str | None = None,
    version: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Repo facts (ecosyste.ms + Scorecard) or package facts (deps.dev)."""
    if owner and repo:
        tool, args = "repo_facts", {"owner": owner, "repo": repo}
        scope = f"{owner}/{repo}"
    elif system and name:
        tool, args = "package_facts", {"system": system, "name": name}
        if version:
            args["version"] = version
        scope = f"{system}:{name}"
    else:
        raise ValueError("code_intel_facts needs owner+repo or system+name")

    refusal = own_repo_refusal(scope, provider=PROVIDER, tool=tool)
    if refusal is not None:
        return refusal
    return await guarded_call(
        provider=PROVIDER, tool=tool, invoke=invoke, args=args, timeout_s=timeout_s
    )
