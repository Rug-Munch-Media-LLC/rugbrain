"""Load the routing table (question shape -> retrieval layer).

Data lives in ``routing_table.json`` next to this module so operators can edit
routing without a code change or deploy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TABLE_PATH = Path(__file__).with_name("routing_table.json")
REQUIRED_LAYER_KEYS = ("server", "probe_url", "call", "answers")


def load_table(path: Path | str | None = None) -> dict[str, Any]:
    """Parse and validate the routing table; raise ValueError if malformed."""
    source = Path(path) if path else TABLE_PATH
    table = json.loads(source.read_text(encoding="utf-8"))
    shapes, layers = table.get("shapes"), table.get("layers")
    if not shapes or not layers:
        raise ValueError(f"routing table {source} has no shapes/layers")
    if table.get("default_layer") not in layers:
        raise ValueError(f"default_layer {table.get('default_layer')!r} is not a defined layer")
    for shape in shapes:
        if shape.get("layer") not in layers:
            raise ValueError(f"shape {shape.get('id')!r} points at undefined layer {shape.get('layer')!r}")
    for name, layer in layers.items():
        missing = [k for k in REQUIRED_LAYER_KEYS if k not in layer]
        if missing:
            raise ValueError(f"layer {name!r} missing {missing}")
        if not layer["call"].get("tool"):
            raise ValueError(f"layer {name!r} declares no call tool")
    return table
