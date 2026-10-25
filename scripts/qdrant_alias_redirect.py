#!/usr/bin/env python3
"""Point an empty base name at its populated ``_8b`` twin.

Qdrant cannot alias a name that still exists as a collection, so applying means
(1) prove the base holds 0 points, (2) drop the empty base, (3) create the
alias. Reads that still say ``live_threats`` then hit ``live_threats_8b``.
Nothing is deleted that holds a point — that is the whole guard.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.collection_inventory import _get, inventory  # noqa: E402

URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")


def main(argv: list[str]) -> int:
    plan = [(r["name"], r["twin"]) for r in inventory(os.getenv("RUG_BRAIN_REPO", "."))
            if r["verdict"] == "redirect" and r["twin"]]
    for base, twin in plan:
        print(f"alias {base} -> {twin}")
    if "--apply" not in argv:
        print(f"\nDRY RUN — {len(plan)} alias(es). Re-run with --apply.")
        return 0
    for base, twin in plan:
        if int(_get(f"/collections/{base}")["points_count"] or 0) != 0:
            print(f"REFUSED {base}: not empty", file=sys.stderr)
            continue
        urllib.request.urlopen(urllib.request.Request(f"{URL}/collections/{base}", method="DELETE"), timeout=20)
        urllib.request.urlopen(urllib.request.Request(
            f"{URL}/collections/aliases", method="POST",
            headers={"content-type": "application/json"},
            data=json.dumps({"actions": [{"create_alias": {
                "collection_name": twin, "alias_name": base}}]}).encode()), timeout=20)
        print(f"aliased {base} -> {twin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
