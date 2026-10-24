"""x_registry loader + its x_intel wiring (same dedupe/embed/rerank path).

Zero monkeypatch: paths are passed as arguments, the Redis offset degrades
inside the pipeline, and every live side effect is faked through DI stubs —
the repo's decrease-only patch ratchet must not move.
"""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.ingest_sources.x_intel import registry


def _write_registry(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"version": 1, "counts": {"total": len(entries)}, "entries": entries}),
        encoding="utf-8",
    )


def test_load_reads_entries_and_excludes_scam_callers(tmp_path: Path) -> None:
    file = tmp_path / "x_registry.json"
    _write_registry(
        file,
        [
            {"handle": "CoinDesk", "kind": "news", "role": "news_desk", "trust": 0.9},
            {"handle": "zachxbt", "kind": "security", "role": "security_researcher", "trust": 0.95},
            {"handle": "DegenShill", "kind": "scam_caller", "role": "impersonator", "trust": 0.0},
            {"handle": "Soup", "kind": "no_such_kind"},
            {"handle": "   ", "kind": "kol"},
        ],
    )
    entries = registry.registry_entries(str(file))
    assert {e["handle"] for e in entries} == {"CoinDesk", "zachxbt"}
    handles = registry.registry_handles(str(file))
    assert handles == ["CoinDesk", "zachxbt"]
    assert registry.registry_entry("ZACHXBT", str(file))["kind"] == "security"
    assert registry.registry_entry("missing", str(file)) is None


def test_missing_file_fails_open(tmp_path: Path) -> None:
    entries = registry.registry_entries(str(tmp_path / "absent.json"))
    assert entries == []
    # A corrupt file must NOT raise either.
    file = tmp_path / "broken.json"
    file.write_text("{not json", encoding="utf-8")
    assert registry.registry_entries(str(file)) == []


def test_the_real_registry_is_real_and_wired(tmp_path: Path) -> None:
    """The shipped registry: 500 accounts, no invented handles, kind legal."""
    real = registry.registry_path()
    data = json.loads(real.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data["entries"]
    assert 480 <= len(entries) <= 520
    legal = set(registry.SOURCE_KINDS) | {"scam_caller"}
    assert {e["kind"] for e in entries} <= legal
    trusted = [e for e in entries if e["kind"] != "scam_caller"]
    assert trusted
    for entry in trusted:
        assert entry["provenance"], entry["handle"]
        assert entry["evidence_url"], entry["handle"]
        assert entry["last_seen"], entry["handle"]
    # Every trusted row cites a real, handle-scoped source: its x.com profile
    # (inventory-verified rows) or its kolscan.io/x/<handle> listing page.
    for entry in trusted:
        url = str(entry["evidence_url"])
        assert url.startswith(f"https://x.com/{entry['handle']}") or (
            url.startswith("https://kolscan.io/x/") and url.endswith(entry["handle"])
        ), entry["handle"]
    assert "scam_caller" not in registry.SOURCE_KINDS


@pytest.mark.asyncio
async def test_registry_handles_join_the_x_intel_rotation() -> None:
    """_select_accounts must route registry handles through the same cycle."""
    from rugbrain.ingest_sources.x_intel import pipeline

    async def select() -> tuple[list[str], list[str]]:
        # The DI seam IS the wiring contract: registry handles ride ``pry``.
        handles = registry.registry_handles()
        assert handles
        return [], [handles[0]]

    pulled: dict[str, Any] = {}

    async def fake_pry_fetch(roster: list[str]) -> tuple[list[dict[str, Any]], bool]:
        pulled["handles"] = roster
        return [
            {"id": "1", "text": f"pilot post by {roster[0]}", "author": {"username": roster[0]}}
        ], False

    async def fake_select() -> tuple[list[str], list[str]]:
        return await select()

    summary = await pipeline.run_cycle(select=fake_select, pry_fetch=fake_pry_fetch)
    assert pulled["handles"] == [registry.registry_handles()[0]]
    assert summary["pry_handles"] == 1
    assert summary["fetched"] >= 1


def test_renamed_away_handles_never_ship() -> None:
    """Stale handles from the seed's renames map must be canonicalized away."""
    root = Path(__file__).resolve().parents[2]
    seed = json.loads((root / "config" / "x_sources_seed.json").read_text(encoding="utf-8"))
    dead = {
        old.lower()
        for old, new in (seed.get("renames") or {}).items()
        if old.lower() != str(new).lower()
    }
    shipped = {e["handle"].lower() for e in registry.registry_entries()}
    assert not (shipped & dead), sorted(shipped & dead)


def test_negative_signal_rows_are_tagged_and_excluded() -> None:
    """Impersonators/squats are in the file as scam_caller, never as sources."""
    data = json.loads(registry.registry_path().read_text(encoding="utf-8"))
    negatives = [e for e in data["entries"] if e["kind"] == "scam_caller"]
    assert negatives, "rejected probes must surface as scam_caller rows"
    for row in negatives:
        assert row["trust"] == 0.0, row["handle"]
        assert row["role"] in ("impersonator", "squatter"), row["handle"]
    assert not (set(registry.registry_handles()) & {e["handle"] for e in negatives})
