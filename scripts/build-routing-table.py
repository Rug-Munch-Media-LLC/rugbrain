#!/usr/bin/env python3
"""Resolve intent-level routing intents into real tool/argument names.

The table stays data: this script only maps `args_intent` roles ("query",
"limit", "library") onto the argument names the live schema actually declares,
so we never invent a parameter name. Re-run after a server changes shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "rugbrain" / "routing_table.json"
FIXTURE = ROOT / "tests" / "fixtures" / "layer_inventory.json"


def resolve(layer: dict, schemas: dict) -> dict:
    served = schemas.get(layer["server"], {})
    tool = layer["call"]["tool"]
    declared = served.get(tool)
    args, unresolved = {}, []
    for role, value in layer["call"].get("args_intent", {}).items():
        candidates = [role, *layer.get("arg_aliases", {}).get(role, [])]
        key = next((c for c in candidates if declared and c in declared), None)
        if key:
            args[key] = value
        else:
            unresolved.append(role)
    layer["call"]["args"] = args
    layer["call"]["declared_args"] = declared or []
    layer["call"]["unresolved_args"] = unresolved
    return layer


def main() -> int:
    table = json.loads(SRC.read_text())
    schemas = json.loads(FIXTURE.read_text()).get("schemas", {})
    for layer in table["layers"].values():
        resolve(layer, schemas)
    table["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    SRC.write_text(json.dumps(table, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    bad = {k: v["call"]["unresolved_args"] for k, v in table["layers"].items() if v["call"]["unresolved_args"]}
    print("unresolved intents:", bad or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
