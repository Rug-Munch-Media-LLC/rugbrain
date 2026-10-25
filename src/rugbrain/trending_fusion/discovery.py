"""Multi-source candidate discovery for the one trending engine.

Two independent sources per chain so a single outage cannot empty a chain.
Every adapter returns the same row contract consumed by ``fuse.py``:
``{source, chain, token_address, symbol, signals:{...}, paid}``.
A failing or empty source is named in ``missing`` -- never scored as zero.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain.trending_fusion.geckoterminal import geckoterminal_rows
from rugbrain.trending_fusion.indexed_pairs import indexed_pair_rows

Source = tuple[str, Callable[[str], Awaitable[list[dict[str, Any]]]]]
SOURCES: tuple[Source, ...] = (
    ("geckoterminal", geckoterminal_rows),
    ("rmi_indexed_pairs", indexed_pair_rows),
)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate (source, chain, address) rows, keeping the max signal."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("source")), str(row.get("chain")), str(row.get("token_address")))
        slot = merged.get(key)
        if slot is None:
            merged[key] = {**row, "signals": dict(row.get("signals") or {})}
            continue
        for name, value in (row.get("signals") or {}).items():
            try:
                slot["signals"][name] = max(float(slot["signals"].get(name, float("-inf"))), float(value))
            except (TypeError, ValueError):
                continue
        slot["paid"] = bool(slot.get("paid")) or bool(row.get("paid"))
    return list(merged.values())


async def gather_rows(chain: str, sources: tuple[Source, ...] | None = None) -> dict[str, Any]:
    """Fan out over the adapters; return rows plus an honest missing-source map."""
    rows: list[dict[str, Any]] = []
    missing: dict[str, str] = {}
    ok: list[str] = []
    for name, fetch in sources if sources is not None else SOURCES:
        try:
            found = await fetch(chain)
        except Exception as exc:
            missing[name] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        if not found:
            missing[name] = "adapter returned 0 candidates"
            continue
        rows.extend(found)
        ok.append(name)
    return {"rows": dedupe_rows(rows), "missing": missing, "sources_ok": ok,
            "scope": f"adapters={','.join(ok) or 'none'}"}
