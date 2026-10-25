"""Scope guard: these providers never answer questions about OUR repos."""
from __future__ import annotations

from typing import Any

from rugbrain.intel_apps.envelope import STATUS_REFUSED, envelope

OWN_ORGS = ("rugmunchmedia", "rugmunch", "cryptorugmunch", "rugbrain")
OWN_REPOS = (
    "rugbrain",
    "rmi-backend",
    "rmi-frontend",
    "pryscraper",
    "walletpress",
    "fleet-infra",
    "fleet-template",
    "contextcrypto",
    "degenfeed-web",
    "x402-gateway",
    "rmi-mcp-x402",
)
OWN_PREFIXES = ("internal/", "internal-lessons/", "docs:")


def own_repo_refusal(target: str, *, provider: str, tool: str) -> dict[str, Any] | None:
    """Refuse outright when the target is ours; zoekt/serena own that surface."""
    probe = (target or "").strip().lower()
    if not probe:
        return None
    ours = (
        any(probe.startswith(p) for p in OWN_PREFIXES)
        or any(probe.startswith(o + "/") for o in OWN_ORGS)
        or probe in OWN_REPOS
        or probe.split("/")[-1] in OWN_REPOS
    )
    if not ours:
        return None
    return envelope(
        provider=provider,
        tool=tool,
        status=STATUS_REFUSED,
        code="own_repo_out_of_scope",
        reason=(
            f"{provider}.{tool} is not used for our own repos "
            "— use zoekt (exact) or serena (symbols) instead"
        ),
    )
