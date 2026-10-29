#!/usr/bin/env python3
"""List, and only then delete, the truly dead collections.

Dry run is the default: it prints the plan. ``--delete`` requires ``--yes`` and
refuses any name that has points or a consumer. ``--write-allow`` records the
current orphans as a ratchet baseline so the guard test fails only on NEW ones.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.collection_inventory import _get, inventory  # noqa: E402

ALLOW = os.path.join(os.path.dirname(__file__), "..", "config", "collection_guard_allow.json")


def main(argv: list[str]) -> int:
    rows = inventory(os.getenv("RUG_BRAIN_REPO", "."))
    dead = [r for r in rows if r["verdict"] in ("retire", "orphan", "fixture", "rebuild", "thin", "empty-advertised", "redirect")]
    for r in rows:
        print(f"{r['verdict']:<17} {r['name']:<42} {r['points']:>9} dim={r['dim']:<5} twin={r['twin_points']}")
    if "--write-allow" in argv:
        os.makedirs(os.path.dirname(ALLOW), exist_ok=True)
        snapshot = {
            "empty": sorted(r["name"] for r in rows
                            if r["verdict"] in ("empty-advertised", "redirect")),
            "orphans": sorted(r["name"] for r in rows if r["verdict"] == "orphan"),
            "fixture_leak": sorted(r["name"] for r in rows
                                   if r["verdict"] == "fixture" and r["consumers"]),
        }
        with open(ALLOW, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        print(f"wrote {ALLOW}")
    if "--delete" not in argv:
        print(f"\nDRY RUN — {len(dead)} candidate(s); nothing deleted.")
        return 0
    if "--yes" not in argv:
        print("refusing: --delete needs --yes", file=sys.stderr)
        return 2
    failed = 0
    for r in rows:
        if r["verdict"] != "retire":
            continue
        if r["points"] or r["consumers"]:
            print(f"skip {r['name']}: points={r['points']} consumers={r['consumers']}")
            failed += 1
            continue
        _get("/collections/" + r["name"], {})  # GET is not a delete; placeholder call
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request(f"{os.getenv('QDRANT_URL', 'http://127.0.0.1:6334')}/collections/{r['name']}",
                                   method="DELETE"), timeout=20)
        print(f"deleted {r['name']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
