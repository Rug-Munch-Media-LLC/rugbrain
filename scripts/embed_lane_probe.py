#!/usr/bin/env python3
"""Resolve THE corpus embedding lane and prove it emits 4096 dims.

One resolution step: probe each candidate lane, require ``dim == 4096``, and
abort naming the lane and the reason. There is no silent 1024-d fallback.

Usage:  python scripts/embed_lane_probe.py [--require local|hosted|any] [--json]
Exit 0 = a 4096-d lane answered; 1 = not, with the failing reasons printed.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

WANT = 4096
SF_KEY_FILE = os.getenv("SILICONFLOW_KEY_FILE", "/srv/secrets/siliconflow_api_key")
SF_URL = os.getenv("SILICONFLOW_EMBED_URL", "https://api.siliconflow.com/v1").rstrip("/")
SF_MODEL = os.getenv("SILICONFLOW_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
LOCAL_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8024/v1").rstrip("/")
LOCAL_ENV_FILES = ("/srv/models/embed-lane.env", "/srv/secrets/embed-lane.env")


def _read_env_file(path: str, names: tuple[str, ...]) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if key.strip() in names and value.strip():
                    return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _local_key() -> str:
    for path in LOCAL_ENV_FILES:
        value = _read_env_file(path, ("LLAMA_EMBED_KEY", "SHIM_KEY"))
        if value:
            return value
    for name in ("LLAMA_EMBED_KEY", "QWEN_EMBED_API_KEY", "LOCAL_EMBED_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _sf_key() -> str:
    value = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if value:
        return value
    try:
        with open(SF_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def probe_lane(name: str, url: str, model: str, key: str) -> dict:
    """POST one text and return {lane,url,model,status,dim,ms,reason}."""
    out = {"lane": name, "url": url, "model": model, "status": 0, "dim": 0, "ms": 0, "reason": ""}
    if not key:
        out["reason"] = "no key resolved (lane not attempted)"
        return out
    body = {"model": model, "input": ["corpus lane probe"], "encoding_format": "float"}
    started = time.perf_counter()
    try:
        resp = httpx.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=30.0)
        out["ms"] = round((time.perf_counter() - started) * 1000)
        out["status"] = resp.status_code
        if resp.status_code != 200:
            out["reason"] = f"http {resp.status_code}: {resp.text[:160]}"
            return out
        data = resp.json().get("data") or []
        vec = (data[0] or {}).get("embedding") if data else None
        out["dim"] = len(vec or [])
        if out["dim"] != WANT:
            out["reason"] = f"dim {out['dim']} != {WANT}"
        return out
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        out["ms"] = round((time.perf_counter() - started) * 1000)
        out["reason"] = f"{type(exc).__name__}: {exc}"[:160]
        return out


def resolve(require: str = "any") -> dict:
    candidates = []
    if require in ("any", "hosted"):
        candidates.append(probe_lane("hosted-siliconflow.com", f"{SF_URL}/embeddings", SF_MODEL, _sf_key()))
    if require in ("any", "local"):
        candidates.append(probe_lane("local-8024", f"{LOCAL_URL}/embeddings", "qwen3-embedding-8b", _local_key()))
    ok = [c for c in candidates if c["dim"] == WANT and not c["reason"]]
    result = {"want_dim": WANT, "require": require, "ok": bool(ok), "lanes": candidates}
    if not ok:
        detail = "; ".join(f"{c['lane']} -> {c['reason'] or 'dim ' + str(c['dim'])}" for c in candidates)
        result["error"] = f"NO 4096-d corpus embed lane ({require}): {detail}"
    else:
        result["chosen"] = ok[0]["lane"]
    return result


def main(argv: list[str]) -> int:
    require = "any"
    if "--require" in argv:
        require = argv[argv.index("--require") + 1]
    result = resolve(require)
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    else:
        for c in result["lanes"]:
            print(f"{c['lane']:<26} {c['url']:<52} status={c['status']} dim={c['dim']} {c['ms']}ms"
                  + (f"  reason={c['reason']}" if c["reason"] else ""))
        print("CHOSEN:", result.get("chosen") or "none")
    if not result["ok"]:
        print("ABORT:", result["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
