"""route_question — one documented door to every retrieval layer."""

from __future__ import annotations

from typing import Any

from rugbrain.layer_probe import probe_layer
from rugbrain.routing_classify import classify
from rugbrain.routing_table import load_table
from rugbrain.rugbrain_mcp.app import mcp


@mcp.tool(name="route_question")
def route_question(question: str, probe: bool = True) -> dict[str, Any]:
    """Tell me which retrieval layer answers this question, with the exact call.

    Use BEFORE searching. Returns the layer (rugbrain vector corpus, zoekt,
    serena, metamcp-code, docs-mcp, context7, deepwiki, context-crypto), the
    exact tool + arguments to call, and `degraded: true` with `use_instead`
    when that layer is unreachable (probed once, cached 60 s).

    Args:
        question: The question, verbatim — routing matches on its shape.
        probe: Set false to skip the reachability probe (fast, offline).
    """
    table = load_table()
    picked = classify(question, table)
    layer = table["layers"][picked["layer"]]
    args = {
        key: (value.replace("<question>", question) if isinstance(value, str) else value)
        for key, value in layer["call"].get("args", {}).items()
    }
    health = probe_layer(layer["probe_url"]) if probe else {"reachable": None, "cached": False, "latency_ms": None}
    result: dict[str, Any] = {
        "question": question,
        "shape": picked["shape"],
        "confidence": picked["confidence"],
        "matched_terms": picked["matched"],
        "layer": picked["layer"],
        "server": layer["server"],
        "call": {"tool": layer["call"]["tool"], "args": args},
        "answers": layer["answers"],
        "not_for": layer.get("not_for", ""),
        "probe": health,
        "degraded": health["reachable"] is False,
        "table_version": table.get("version"),
    }
    if result["degraded"]:
        alt = table["layers"].get(layer.get("fallback_layer", ""))
        result["use_instead"] = ({"layer": layer["fallback_layer"],
                                  "tool": alt["call"]["tool"] if alt else None})
    return result
