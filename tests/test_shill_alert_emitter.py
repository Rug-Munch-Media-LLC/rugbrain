"""The shill-alert emitter must NEVER emit the same finding twice."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "alert_shill_campaigns", Path(__file__).resolve().parents[1] / "scripts/alert_shill_campaigns.py"
)
alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alert)


def _item(i: str, when: str) -> dict:
    return {"id": i, "text": "100x gem", "metadata": {"risk": 5, "categories": ["guaranteed_returns"]},
            "ingested_at": when}


def test_first_run_emits_everything_and_sets_cursor(tmp_path, monkeypatch):
    state_file, out = tmp_path / "s.json", tmp_path / "a.jsonl"
    monkeypatch.setattr(alert, "STATE", state_file)
    monkeypatch.setattr(alert, "OUT", out)
    assert alert.emit([_item("a", "2026-01-01")], {}, dry_run=False) == 1
    assert json.loads(state_file.read_text())["cursor"] == "2026-01-01"
    assert len(out.read_text().strip().splitlines()) == 1


def test_second_run_is_idempotent(tmp_path, monkeypatch):
    state_file, out = tmp_path / "s.json", tmp_path / "a.jsonl"
    monkeypatch.setattr(alert, "STATE", state_file)
    monkeypatch.setattr(alert, "OUT", out)
    items = [_item("a", "2026-01-01")]
    alert.emit(items, {}, dry_run=False)
    state = alert.load_state()
    assert alert.emit(items, state, dry_run=False) == 0


def test_only_strictly_newer_is_emitted(tmp_path, monkeypatch):
    state_file, out = tmp_path / "s.json", tmp_path / "a.jsonl"
    monkeypatch.setattr(alert, "STATE", state_file)
    monkeypatch.setattr(alert, "OUT", out)
    alert.emit([_item("a", "2026-01-01")], {}, dry_run=False)
    state = alert.load_state()
    n = alert.emit([_item("a", "2026-01-01"), _item("b", "2026-01-02")], state, dry_run=False)
    assert n == 1


def test_dry_run_never_writes(tmp_path, monkeypatch):
    state_file, out = tmp_path / "s.json", tmp_path / "a.jsonl"
    monkeypatch.setattr(alert, "STATE", state_file)
    monkeypatch.setattr(alert, "OUT", out)
    assert alert.emit([_item("a", "2026-01-01")], {}, dry_run=True) == 1
    assert not state_file.exists() and not out.exists()


@pytest.mark.parametrize("bad", ["", "  "])
def test_state_file_corruption_is_survivable(tmp_path, monkeypatch, bad):
    state_file = tmp_path / "s.json"
    state_file.write_text(bad)
    monkeypatch.setattr(alert, "STATE", state_file)
    assert alert.load_state() == {"cursor": "", "seen": []}
