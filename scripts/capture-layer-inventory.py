#!/usr/bin/env python3
"""Capture the live tool inventory (names + argument keys) of every retrieval layer.

CI is offline, so the routing table is validated against this fixture instead of
a live server. HTTP layers are captured with MCP `tools/list`; the metamcp
namespaces are read from the metamcp DB (`tools` table) because their bridge
requires an API key that is only resolved inside opencode.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = Path("/srv/work/repos/fleet-infra/mcp/canonical-mcp.json")
HTTP_LAYERS = ("serena", "zoekt", "docs-mcp", "context7", "deepwiki", "rugbrain-mcp")
DB_LAYERS = (("metamcp-code", "code"), ("metamcp-research", "research"),
             ("metamcp-audit", "audit"), ("metamcp-ops", "ops"),
             ("context-crypto", "code"))
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "layer_inventory.json"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())["opencode_mcp"]["servers"]


def rpc(url: str, payload: dict, session: str | None = None, extra: dict | None = None,
        timeout: int = 20) -> dict:
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    headers.update(extra or {})
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        sid = resp.headers.get("mcp-session-id")
        raw = resp.read().decode("utf-8", "replace")
    for line in raw.splitlines():
        if line.startswith("data: "):
            raw = line[6:]
            break
    out = json.loads(raw)
    if sid:
        out["_session"] = sid
    return out


def props(schema: dict) -> list[str]:
    return sorted((schema or {}).get("properties") or {})


def http_layer(name: str, spec: dict) -> tuple[list[str], dict[str, list[str]]]:
    url, extra = spec["url"], spec.get("headers") or {}
    init = rpc(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "rugbrain-capture", "version": "1"}}}, extra=extra)
    listing = rpc(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  session=init.get("_session"), extra=extra)
    tools = listing["result"]["tools"]
    return sorted(t["name"] for t in tools), {t["name"]: props(t.get("inputSchema")) for t in tools}


def sql(query: str) -> str:
    return subprocess.run(
        ["docker", "exec", "metamcp-pg", "psql", "-U", "metamcp_user", "-d", "metamcp_db", "-Atc", query],
        check=True, capture_output=True, text=True).stdout.strip()


def db_layer(ns: str) -> tuple[list[str], dict[str, list[str]]]:
    rows = sql(f"""select distinct t.name, t.tool_schema from namespace_tool_mappings nt
      join tools t on t.uuid = nt.tool_uuid join namespaces n on n.uuid = nt.namespace_uuid
      where n.name = '{ns}'""")
    names, schemas = [], {}
    for line in rows.splitlines():
        name, _, raw = line.partition("|")
        names.append(name)
        try:
            schemas[name] = props(json.loads(raw))
        except json.JSONDecodeError:
            schemas[name] = []
    return sorted(names), schemas


def main() -> int:
    servers = manifest()
    inventory: dict[str, list[str]] = {}
    schemas: dict[str, dict[str, list[str]]] = {}
    for name in HTTP_LAYERS:
        try:
            inventory[name], schemas[name] = http_layer(name, servers[name])
            print(f"{name:18} {len(inventory[name]):4} tools (http)", file=sys.stderr)
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"{name:18} UNREACHABLE {exc}", file=sys.stderr)
    for name, ns in DB_LAYERS:
        try:
            inventory[name], schemas[name] = db_layer(ns)
            print(f"{name:18} {len(inventory[name]):4} tools (db:{ns})", file=sys.stderr)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"{name:18} DB-FAIL {exc}", file=sys.stderr)
    payload = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "scripts/capture-layer-inventory.py — MCP tools/list (http) + metamcp tools table (db)",
        "servers": inventory,
        "schemas": schemas,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({sum(len(v) for v in inventory.values())} tools)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
