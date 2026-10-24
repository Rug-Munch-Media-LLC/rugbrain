"""Evidence-side registry rows: security alert posts + kolscan leaderboards.

Both datasets cite per-account live evidence (a status URL or a leaderboard
listing page), which is what makes a handle auditable rather than guessed.

* ``data/exploit_drafts.json``      — per-post security-alert status links
* ``data/kol-intel/kolscan*.json``  — kolscan.io leaderboard snapshots
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
KOLSCAN_DATE = "2026-09-30"
KOLSCAN_FILES: tuple[str, ...] = (
    "kolscan-20260930.json",
    "kolscan-deep-20260930.json",
    "kol-wallets-merged.json",
)
X_POST_RE = re.compile(r"https?://x\.com/([A-Za-z0-9_]{2,15})/status/\d+")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _row(handle: str, name: Any, kind: str, role: str, **extra: Any) -> dict[str, Any]:
    return {"handle": handle, "name": name or handle, "kind": kind, "role": role, **extra}


def from_exploit_drafts() -> list[dict[str, Any]]:
    """Security/alert accounts cited with per-post status links (live evidence)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _read(ROOT / "data" / "exploit_drafts.json") or []:
        if not isinstance(entry, dict):
            continue
        match = X_POST_RE.match(str(entry.get("link") or ""))
        if not match or match.group(1).lower() in seen:
            continue
        handle, date, url = match.group(1), str(entry.get("date") or ""), entry["link"]
        seen.add(handle.lower())
        rows.append(
            _row(
                handle,
                handle,
                "security",
                "security_monitor",
                category="security",
                chain_focus="",
                followers=0,
                trust=0.95,
                source="exploit_drafts",
                provenance=["data/exploit_drafts.json"],
                evidence_url=url,
                evidence=f"alert status post {date} ({entry.get('chain') or 'multi'} chain)",
                last_seen=date or "2026-09-07",
            )
        )
    return rows


def _kolscan_index() -> dict[str, dict[str, Any]]:
    """Union of the kolscan snapshots, ranked by trader evidence."""
    best: dict[str, dict[str, Any]] = {}
    for name in KOLSCAN_FILES:
        rows = _read(ROOT / "data" / "kol-intel" / name) or []
        for snap in rows if isinstance(rows, list) else []:
            handle = str(snap.get("handle") or "").strip().lstrip("@")
            if not handle:
                continue
            extra = snap.get("extra") if isinstance(snap.get("extra"), dict) else {}
            try:
                profit = float(snap.get("profit") or extra.get("pnl") or 0)
                wins = int(snap.get("wins") or extra.get("wins") or 0)
            except (TypeError, ValueError):
                profit, wins = 0.0, 0
            cur = best.setdefault(
                handle.lower(),
                {
                    "handle": handle,
                    "name": snap.get("name") or handle,
                    "chain": snap.get("chain") or "",
                    "address": snap.get("address") or "",
                    "profit": 0.0,
                    "wins": 0,
                    "seen": 0,
                },
            )
            cur["seen"] += 1
            cur["profit"] = max(cur["profit"], profit)
            cur["wins"] = max(cur["wins"], wins)
            cur["address"] = cur["address"] or (snap.get("address") or "")
    return best


def kolscan_kols(known: set[str], limit: int) -> list[dict[str, Any]]:
    """kolscan leaderboard evidence: real trader handles ranked by realized PnL."""
    index = _kolscan_index()
    for handle in known:
        index.pop(handle.lower(), None)
    ranked = sorted(
        index.values(), key=lambda c: (c["profit"], c["wins"], c["seen"]), reverse=True
    )[:limit]
    rows: list[dict[str, Any]] = []
    for cur in ranked:
        handle, profit, wins = cur["handle"], cur["profit"], cur["wins"]
        track = (
            f"kolscan {KOLSCAN_DATE}: pnl=${profit:.0f} wins={wins} "
            f"outcomes={cur['seen']} snapshot(s)"
            if profit > 0 or wins > 0
            else f"kolscan {KOLSCAN_DATE}: listed trader, {cur['seen']} snapshot(s) "
            f"(wallet {cur['address'][:10] or '-'} on record)"
        )
        rows.append(
            _row(
                handle,
                cur["name"],
                "kol",
                "trader",
                category="kol",
                chain_focus=cur["chain"] or "solana",
                followers=0,
                trust=0.4,
                source="kolscan_20260930",
                provenance=[f"data/kol-intel/{KOLSCAN_FILES[0]}"],
                evidence_url=f"https://kolscan.io/x/{handle}",
                evidence=track,
                last_seen=KOLSCAN_DATE,
            )
        )
    return rows


__all__ = ["KOLSCAN_DATE", "from_exploit_drafts", "kolscan_kols"]
