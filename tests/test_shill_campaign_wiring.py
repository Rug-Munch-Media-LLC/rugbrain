"""The alert feed must carry shill_score + campaign_id for news/search."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _alert():
    spec = importlib.util.spec_from_file_location(
        "alert_shill_campaigns", Path(__file__).resolve().parents[1] / "scripts/alert_shill_campaigns.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item(i: str, text: str, channel: str) -> dict:
    return {"id": i, "text": text, "ingested_at": "2026-10-12T00:00:00+00:00",
            "metadata": {"risk": 5, "channel": channel, "date": "2026-10-12T00:00:00+00:00"}}


def test_emit_stamps_shill_score_and_campaign_id(tmp_path, monkeypatch):
    alert = _alert()
    monkeypatch.setattr(alert, "STATE", tmp_path / "s.json")
    monkeypatch.setattr(alert, "OUT", tmp_path / "a.jsonl")
    pitch = "100x gem guaranteed returns, buy now"
    assert alert.emit([_item("a", pitch, "-1"), _item("b", pitch, "-2")], {}, dry_run=False) == 2
    rows = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert all("shill_score" in r["metadata"] and "campaign_id" in r["metadata"] for r in rows)
    assert rows[0]["metadata"]["shill_score"] == 0.5
    assert len({r["metadata"]["campaign_id"] for r in rows}) == 1, "same pitch must share a campaign"


def test_dry_run_stamps_nothing_on_disk(tmp_path, monkeypatch):
    alert = _alert()
    monkeypatch.setattr(alert, "STATE", tmp_path / "s.json")
    monkeypatch.setattr(alert, "OUT", tmp_path / "a.jsonl")
    assert alert.emit([_item("a", "100x gem", "-1")], {}, dry_run=True) == 1
    assert not (tmp_path / "a.jsonl").exists()
