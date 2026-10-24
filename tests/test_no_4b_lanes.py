"""8B-ONLY GUARD — the 4B/2560 generation is dead (operator directive 2026-10-11).

These tests fail the build if any runtime code, script, compose file or doctrine
doc resurrects a 4B model name, so agents can never again auto-select the
retired embedding/rerank lane. Live-state descriptors (corpus/entries_*,
qdrant_store/lane_*) document REAL collection dims and are excluded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"qwen3-(embedding|reranker)-4b|qwen3-4b|qwen4b", re.IGNORECASE)
LIVE_STATE = re.compile(r"(src/rugbrain/corpus/|qdrant_store/lane_|qdrant_store/write_lane)")
# Files that name the retired 4B lane ON PURPOSE to police or refuse it.
ALLOWLIST = {
    "tests/test_embed_fallback_pools.py",
    "tests/test_no_4b_lanes.py",
    "tests/test_embedder_tier_contracts.py",
    "tests/test_dim_drift.py",
    "src/rugbrain/dim_policy.py",
    "src/rugbrain/dim_drift.py",
    "src/rugbrain/dim_drift_nodes.py",
    "src/rugbrain/dim_drift_shapes.py",
    "src/rugbrain/qdrant_store/lane_resolver.py",
}
SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".example"}


def _runtime_files() -> list[Path]:
    out = []
    for base in ("src", "scripts", "tests"):
        for p in (REPO / base).rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in {".venv", "__pycache__", "node_modules", ".git"} for part in p.parts):
                continue
            rel = str(p.relative_to(REPO))
            if LIVE_STATE.search(rel) or rel in ALLOWLIST:
                continue
            out.append(p)
    for name in ("docker-compose.yml", ".env.example"):
        p = REPO / name
        if p.exists():
            out.append(p)
    return out


@pytest.mark.parametrize("path", _runtime_files(), ids=lambda p: str(p))
def test_no_4b_model_reference(path: Path) -> None:
    if str(path.relative_to(REPO)) in ALLOWLIST:
        pytest.skip("retirement-policing file")
    hits = [ln for ln in path.read_text(errors="replace").splitlines() if FORBIDDEN.search(ln)]
    assert not hits, f"4B reference in {path}: {hits[:3]}"


def test_rerank_hosted_defaults_to_8b() -> None:
    import rugbrain.rerank_hosted as rh

    assert "8B" in rh.RERANK_HOSTED_MODEL


def test_alert_embed_defaults_to_8b() -> None:
    from rugbrain.alerts import config as cfg

    assert "8b" in cfg.QWEN_EMBED_MODEL_NAME.lower()
