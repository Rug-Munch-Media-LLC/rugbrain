"""Registry guard: an advertised name must hold points, and orphans must not grow.

Two failures this prevents, both observed live on 2026-10-25:
  * ``news_v2`` / ``live_threats`` are advertised (probes, lane table, routes)
    and are read directly, but hold 24 / 0 points while the ``_8b`` twins hold
    299,074 / 11,164. A reader gets silence and nothing complains.
  * a collection with points and no consumer is pure cost — but deleting it is
    the operator's call, so it is a ratchet: the recorded baseline may shrink,
    never grow.

Qdrant is a live dependency; without it the test skips rather than lying green.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.collection_inventory import inventory  # noqa: E402

ALLOW = os.path.join(os.path.dirname(__file__), "..", "config", "collection_guard_allow.json")


def _rows():
    try:
        return inventory(os.getenv("RUG_BRAIN_REPO", "."))
    except Exception as exc:  # noqa: BLE001 - live store absent in CI
        pytest.skip(f"qdrant unreachable: {exc}")


def test_advertised_collections_hold_points_and_orphans_do_not_grow():
    rows = _rows()
    base = {}
    if os.path.exists(ALLOW):
        with open(ALLOW) as fh:
            base = json.load(fh)

    empty = sorted(f"{r['name']}(0 pts, twin={r['twin_points']})" for r in rows
                   if r["verdict"] in ("empty-advertised", "redirect"))
    new_empty = [e for e in empty if e.split("(")[0] not in base.get("empty", [])]
    assert not new_empty, "advertised collection(s) with 0 points: " + ", ".join(new_empty)

    orphans = sorted(r["name"] for r in rows if r["verdict"] == "orphan")
    new = [o for o in orphans if o not in base.get("orphans", [])]
    assert not new, f"new orphan collection(s) with points but no consumer: {new}"

    leaks = sorted(r["name"] for r in rows
                   if r["verdict"] == "fixture" and r["consumers"])
    new_leaks = [n for n in leaks if n not in base.get("fixture_leak", [])]
    assert not new_leaks, f"test fixture(s) referenced from production modules: {new_leaks}"
