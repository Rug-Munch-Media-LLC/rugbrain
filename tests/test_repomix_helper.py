"""Repomix helper is gated: no blind pack, no oversized tree, real pack works."""

from __future__ import annotations

from rugbrain import repomix_helper


async def test_flag_required(tmp_path):
    assert (await repomix_helper.pack_repo(str(tmp_path)))["refusal"] == "flag_required"


async def test_env_flag_required(tmp_path, monkeypatch):
    monkeypatch.delenv("RUGBRAIN_REPOMIX", raising=False)
    assert (await repomix_helper.pack_repo(str(tmp_path), allow=True))["refusal"] == "flag_disabled"


async def test_too_many_files_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("RUGBRAIN_REPOMIX", "1")
    for index in range(3):
        (tmp_path / f"m{index}.py").write_text("x = 1\n")
    result = await repomix_helper.pack_repo(str(tmp_path), allow=True, max_files=1)
    assert (result["refusal"], result["files"]) == ("too_many_files", 3)


async def test_real_pack_returns_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("RUGBRAIN_REPOMIX", "1")
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    result = await repomix_helper.pack_repo(str(tmp_path), allow=True)
    assert result["ok"], result
    assert result["summary"]["packed_files"] >= 1
    assert result["bytes"] > 0
