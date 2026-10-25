"""Delegation: hand a question to the app that OWNS that capability.

Two RugBrain paths duplicated another app's job:

* exact string / regex matching  -> zoekt owns it (trigram index, committed code)
* "what IS this symbol / who references it" -> serena owns it (LSP)

This module keeps RugBrain's own behaviour alive as a typed fallback instead of
re-implementing a worse version of the owner. The fallback is injected (DI), so
tests never need a live app and production never monkeypatches anything.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rugbrain.fleet_layers.envelope import layer_response
from rugbrain.fleet_layers.native import native_search
from rugbrain.fleet_layers.serena import serena_symbols
from rugbrain.fleet_layers.zoekt import zoekt_search

OWNERS = {"exact": "zoekt", "symbol": "serena", "compress": "cc-compress"}
ALL_DOWN = "do not substitute a plausible answer: report the miss and name the layers tried"


def delegate(
    kind: str,
    *,
    query: str,
    project: str = "",
    fallback: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Call the owning app first; on refusal fall back to RugBrain's own search.

    Success carries ``source`` = the app that answered and ``delegated_to``.
    A fallback answer keeps ``source="rugbrain"`` plus ``fell_back_from``, so an
    assistant can always tell WHICH app answered its question.
    """
    if kind not in ("exact", "symbol"):
        return layer_response(source="delegate", error_code="unsupported_kind", detail=f"kind={kind!r}; use exact|symbol", fallback=ALL_DOWN)
    owner = zoekt_search(query) if kind == "exact" else serena_symbols(project, query)
    if owner.get("ok"):
        return {**owner, "delegated_to": owner["source"], "fell_back_from": None, "owner_error": None}
    fb = (fallback or native_search)(query)
    if fb.get("ok"):
        return {**fb, "delegated_to": fb["source"], "fell_back_from": owner["source"], "owner_error": owner.get("error")}
    out = layer_response(
        source="delegate",
        error_code="all_layers_unavailable",
        detail=f"owner {owner['source']}={owner.get('error')}; fallback {fb['source']}={fb.get('error')}",
        fallback=ALL_DOWN,
    )
    out.update({"delegated_to": OWNERS[kind], "fell_back_from": owner["source"],
                "owner_error": owner.get("error"), "fallback_error": fb.get("error")})
    return out
