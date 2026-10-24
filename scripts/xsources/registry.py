"""Build ``config/x_registry.json`` — the top ~500 real X crypto accounts.

    python3 scripts/xsources/registry.py

Composition (highest-trust rows first, deduped by handle, capped at 500):

1. verification-passed inventory (``data/x_sources.json``)
2. security alert accounts cited by per-post status links
3. rejected probes — impersonations/squats, negative signal only
4. kolscan.io leaderboard traders (fills to the target size)

Handles are canonicalized through the seed's ``renames`` map. The output is
COMMITTED (``config/``) so ingestion, tests and review all read one artifact;
the ignored ``data/`` tree only holds the raw inputs.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry_evidence import KOLSCAN_DATE, from_exploit_drafts, kolscan_kols
from registry_rows import (
    KINDS,
    REGISTRY,
    TARGET_SIZE,
    from_verified,
    rejected_negative,
    seed_renames,
)

SOURCES: tuple[str, ...] = (
    "data/x_sources.json",
    "data/x_sources_rejected.json",
    "data/exploit_drafts.json",
    f"data/kol-intel/kolscan-{KOLSCAN_DATE.replace('-', '')}.json",
    "config/x_sources_seed.json (renames)",
)


def build() -> dict[str, Any]:
    """Compose the registry payload (deduped by handle, capped to ~500)."""
    renames = seed_renames()
    merged: dict[str, dict[str, Any]] = {}
    for row in from_verified(renames) + from_exploit_drafts() + rejected_negative(renames):
        merged.setdefault(row["handle"].lower(), row)  # first = highest trust
    fills = kolscan_kols(set(merged), max(0, TARGET_SIZE - len(merged)))
    for row in fills:
        merged.setdefault(row["handle"].lower(), row)
    final = list(merged.values())[:TARGET_SIZE]
    return {
        "version": 1,
        "target": TARGET_SIZE,
        "kind_values": list(KINDS),
        "sources": list(SOURCES),
        "method": (
            "No invented handles: kolscan.io leaderboards (2026-09-30), the "
            "inventory-verified build, and security-alert status posts. Handles are "
            "canonicalized through config/x_sources_seed.json renames. Impersonation "
            "rows (trust 0) are negative-signal attribution-education accounts, never "
            "content sources."
        ),
        "counts": {
            "total": len(final),
            "by_kind": dict(Counter(r["kind"] for r in final)),
            "by_role": dict(Counter(r["role"] for r in final)),
            "by_chain": dict(Counter(r["chain_focus"] or "multi" for r in final)),
        },
        "entries": sorted(final, key=lambda r: (r["kind"], -(r["followers"] or 0), r["handle"])),
    }


def main() -> int:
    payload = build()
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
