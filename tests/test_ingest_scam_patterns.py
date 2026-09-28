"""Tests for scripts/ingest_scam_patterns.py.

Three things must hold and are the reason this file exists:
  1. the default (dry-run) path writes NOTHING,
  2. point ids are deterministic so a re-run upserts instead of duplicating,
  3. the funds-lost parser does not read a token TICKER as "billions" —
     the regression guard for 5d097d4, where '15114 BUSD' became
     $15 TRILLION and pinned a fabricated headline figure to the top of the
     pattern list.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_scam_patterns as ing

from rugbrain.scam_patterns import (
    _parse_funds_lost,
    extract_scam_patterns_from_docs,
)

DOCS = [
    {
        "content": "withdraw() sweep drain rescueToken — reentrancy call.value",
        "severity": "critical",
        "metadata": {
            "source_url": "https://x/1",
            "protocol": "Alpha",
            "chain": "EVM",
            "amount_lost_usd": "15114 BUSD",
        },
    },
    {
        "content": "flashloan borrow() aave price manipulation twap",
        "severity": "high",
        "metadata": {"source_url": "https://x/2", "amount_lost_usd": "$1.5M"},
    },
]


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """End-to-end: default run reads, extracts, and touches no Qdrant write."""
    attempts: list[str] = []

    async def fake_read(pool, limit):
        return DOCS

    async def forbidden(*args, **kwargs):
        attempts.append("WRITE_ATTEMPTED")
        raise AssertionError("dry run must not write")

    monkeypatch.setattr(ing, "_read_pool", fake_read)
    monkeypatch.setattr(ing, "_write_patterns", forbidden)
    monkeypatch.setattr(
        "sys.argv", ["ingest_scam_patterns.py", "--payload-only", str(tmp_path / "p.json")]
    )
    assert asyncio.run(ing.main()) == 0
    assert attempts == []
    assert json.loads((tmp_path / "p.json").read_text()), "payload should still be written"


def test_dry_run_reaches_qdrant_write(monkeypatch, tmp_path):
    """Without --write, main() must not call _write_patterns at all."""
    attempts: list[str] = []

    async def fake_read(pool, limit):
        return DOCS

    async def forbidden(*args, **kwargs):
        attempts.append("WRITE_ATTEMPTED")
        return (0, 0)

    monkeypatch.setattr(ing, "_read_pool", fake_read)
    monkeypatch.setattr(ing, "_write_patterns", forbidden)
    monkeypatch.setattr("sys.argv", ["ingest_scam_patterns.py"])
    assert asyncio.run(ing.main()) == 0
    assert attempts == [], "default run must be a dry run"


def test_write_flag_calls_the_writer(monkeypatch):
    """--write is the only way to reach the write path."""
    seen: list[str] = []

    async def fake_read(pool, limit):
        return DOCS

    async def fake_write(patterns, collection, batch=64):
        seen.append(collection)
        return (len(patterns), 0)

    async def fake_embed(texts):
        return [[0.0] * 4096 for _ in texts]

    monkeypatch.setattr(ing, "_read_pool", fake_read)
    monkeypatch.setattr(ing, "_write_patterns", fake_write)
    monkeypatch.setattr(ing, "_embed_8b", fake_embed)
    monkeypatch.setattr("sys.argv", ["ingest_scam_patterns.py", "--write"])
    assert asyncio.run(ing.main()) == 0
    assert seen == [ing.DEFAULT_COLLECTION]


def test_pattern_point_id_is_deterministic():
    a = ing.pattern_point_id("reentrancy:reentrancy-guard-missing")
    b = ing.pattern_point_id("reentrancy:reentrancy-guard-missing")
    assert a == b
    assert a != ing.pattern_point_id("reentrancy:unchecked-external-call")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15114 BUSD", 15114.0),  # 5d097d4: 'B' of the TICKER, not billions
        ("$1.5M", 1_500_000.0),
        ("2.5 billion", 2_500_000_000.0),
        ("~ 50 BNB", 50.0),
        ("3.3 BNB", 3.3),
        (None, 0.0),
    ],
)
def test_parse_funds_lost_regression(raw, expected):
    assert _parse_funds_lost(raw) == pytest.approx(expected)


def test_billions_bug_does_not_top_the_list():
    """The fabricated trillion must not reappear as a headline figure."""
    patterns = extract_scam_patterns_from_docs(DOCS)
    # The BUSD doc alone must read 15,114 — the $1.5M flash-loan doc outranks it.
    # Under 5d097d4 the BUSD doc read 1.5114e13 and topped the list.
    top = max(patterns, key=lambda p: p["funds_lost"])
    assert top["funds_lost"] == pytest.approx(1_500_000.0)
    busd = next(p for p in patterns if "Alpha" in p["affected_protocols"])
    assert busd["funds_lost"] == pytest.approx(15114.0)


def test_empty_indicators_flagged_in_payload():
    """An empty indicator scan must be marked, not silently trusted."""
    pattern = {
        "pattern_id": "governance-attack:governance-attack",
        "attack_type": "governance-attack",
        "technique": "governance-attack",
        "affected_protocols": [],
        "chain": "",
        "severity": "info",
        "funds_lost": 0.0,
        "reference_incidents": [],
        "indicators": [],
        "doc_count": 1,
    }
    assert (
        ing._payload(pattern, indicators_complete=bool(pattern["indicators"]))["metadata"][
            "indicators_complete"
        ]
        is False
    )
    assert (
        ing._payload({**pattern, "indicators": ["blacklist-control"]}, indicators_complete=True)[
            "metadata"
        ]["indicators_complete"]
        is True
    )
