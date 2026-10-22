"""Inventory: every X handle this fleet already references, with provenance.

Sources (all read-only, no network):
  * rugbrain/config/company_channels.json  -> official brand/protocol handles
  * rmi-backend x_intel/config.py          -> TIER1/TIER2 monitored CT accounts
  * rmi-backend x_intel/helpers.py         -> CT_ACCOUNTS curated dict
  * config/x_sources_seed.json             -> curated expansion (this registry)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPOS = ROOT.parent
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,15}$")


def _from_company_channels() -> list[dict[str, Any]]:
    path = ROOT / "config" / "company_channels.json"
    if not path.exists():
        path = ROOT / "data" / "company_channels.json"
    brands = json.loads(path.read_text())["brands"]
    out = []
    for brand in brands:
        handle = (brand.get("x_handle") or "").strip().lstrip("@")
        if HANDLE_RE.match(handle):
            out.append(
                {
                    "handle": handle,
                    "name": brand.get("x_name") or brand.get("brand"),
                    "category": "ecosystem",
                    "site": f"https://{brand['domain']}" if brand.get("domain") else None,
                    "provenance": ["config/company_channels.json"],
                }
            )
    return out


def _from_x_intel_config() -> list[dict[str, Any]]:
    path = ROOT / "src/rugbrain/ingest_sources/x_intel/config.py"
    if not path.exists():
        return []
    text = path.read_text()
    out = []
    for block in ("_TIER1_RAW", "_TIER2_RAW"):
        match = re.search(rf"{block} = os\.getenv\((.*?)\n\)", text, re.S)
        if not match:
            continue
        parts = re.findall(r'"([^"]*)"', match.group(1))[1:]  # skip the env-var name
        raw = re.sub(r"#.*", "", "".join(parts))
        for handle in raw.split(","):
            handle = handle.strip().strip('"').lstrip("@")
            if HANDLE_RE.match(handle):
                out.append(
                    {
                        "handle": handle,
                        "name": None,
                        "category": "kol",
                        "site": None,
                        "provenance": [f"rmi-backend x_intel/config.py:{block}"],
                    }
                )
    return out


def _from_ct_accounts() -> list[dict[str, Any]]:
    path = REPOS / "rmi-backend/app/databus/x_intel/helpers.py"
    if not path.exists():
        return []
    text = path.read_text()
    out = []
    for handle, name in re.findall(r'"handle":\s*"([^"]+)"(?:,\s*"name":\s*"([^"]*)")?', text):
        handle = handle.strip().lstrip("@")
        if HANDLE_RE.match(handle):
            out.append(
                {
                    "handle": handle,
                    "name": name or None,
                    "category": "kol",
                    "site": None,
                    "provenance": ["rmi-backend x_intel/helpers.py:CT_ACCOUNTS"],
                }
            )
    return out


def _from_seed(seed: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**entry, "provenance": ["config/x_sources_seed.json"]}
        for entry in seed.get("entries", [])
        if HANDLE_RE.match(entry.get("handle", ""))
    ]


def collect(seed: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge every source, dedupe case-insensitively, keep first-seen casing."""
    raw = _from_seed(seed) + _from_company_channels() + _from_x_intel_config() + _from_ct_accounts()
    merged: dict[str, dict[str, Any]] = {}
    for item in raw:
        key = item["handle"].lower()
        if key in merged:
            cur = merged[key]
            cur["provenance"] = sorted(set(cur["provenance"]) | set(item["provenance"]))
            cur["name"] = cur.get("name") or item.get("name")
            cur["site"] = cur.get("site") or item.get("site")
            continue
        merged[key] = item
    stat = {
        "raw_records": len(raw),
        "unique_handles": len(merged),
        "by_source": {},
    }
    for item in merged.values():
        for src in item["provenance"]:
            stat["by_source"][src] = stat["by_source"].get(src, 0) + 1
    return list(merged.values()), stat
