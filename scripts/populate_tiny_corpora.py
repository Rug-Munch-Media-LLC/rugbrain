#!/usr/bin/env python3
"""Populate tiny rugbrain corpora from material already owned (no scraping)."""
import glob, hashlib, json, os, re, urllib.error, urllib.request, uuid
from datetime import datetime, timezone

QD = "http://127.0.0.1:6334"
EMB = "http://127.0.0.1:8024/v1/embeddings"
NS = uuid.UUID("6f6d2f7a-0f7a-4f2a-9f3a-7c1d5b8e9a10")
SOL = re.compile(r"solana|spl token|raydium|orca\b|jupiter|pump\.fun|anchor program|metaplex|token-2022|sanctum|serum", re.I)
EVM = re.compile(r"solidity|evm|ethereum|erc-?\d{3}|reentrancy|smart contract|defi|\bamm\b|lending|vault|oracle|bridge", re.I)
DESIGN = re.compile(r"architecture|design|invariant|upgrade|governance|oracle|mechanism|economic|incentive|access control|emergency|pause|timelock|circuit|collateral|liquidation", re.I)
KEYS = ("text", "content", "document", "page_content", "chunk", "body", "summary", "description", "pattern", "title")


def http(method, url, body=None, timeout=180, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def shim_key():
    if os.environ.get("SHIM_KEY"):
        return os.environ["SHIM_KEY"]
    for p in glob.glob("/etc/systemd/system/*.service") + glob.glob("/root/.env*"):
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        m = re.search(r"SHIM_KEY\s*=\s*\"?([A-Za-z0-9_\-]{8,})", t)
        if m:
            return m.group(1)
    return ""


def text_of(p):
    for k in KEYS:
        v = p.get(k)
        if isinstance(v, str) and len(v) > 120:
            return v
    best = ""
    for v in p.values():
        if isinstance(v, str) and len(v) > len(best):
            best = v
    return best


def scroll(coll, cap, batch=512):
    off, seen = None, 0
    while seen < cap:
        body = {"limit": min(batch, cap - seen), "with_payload": True, "with_vector": False}
        if off is not None:
            body["offset"] = off
        r = http("POST", f"{QD}/collections/{coll}/points/scroll", body)
        pts = r["result"]["points"]
        for p in pts:
            yield p
        seen += len(pts)
        off = r["result"].get("next_page_offset")
        if not off or not pts:
            return


def digests(coll):
    out = set()
    for p in scroll(coll, 200000, 256):
        t = text_of(p.get("payload") or {})
        if t:
            out.add(hashlib.sha1(t.strip().encode()).hexdigest())
    return out


def embed(texts, key):
    for attempt in (1, 2):
        hdrs = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            r = http("POST", EMB, {"model": "qwen3-embedding-8b", "input": texts}, headers=hdrs)
            return [d["embedding"] for d in r["data"]]
        except urllib.error.HTTPError as e:
            body = e.read()[:160].decode("utf-8", "replace")
            print("EMBED-ERR", e.code, body)
            if attempt == 2:
                raise
    return []


def harvest(src, cap, match, seen):
    out = []
    for p in scroll(src, cap):
        pl = p.get("payload") or {}
        t = text_of(pl)
        if not t or len(t) < 200 or not match.search(t):
            continue
        h = hashlib.sha1(t.strip().encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append((src, p["id"], t, pl))
    return out


def build(coll, spec, key):
    seen = digests(coll)
    have = len(seen)
    need = max(0, spec["min"] - have)
    picks = []
    for src, cap in spec["srcs"]:
        if len(picks) >= need:
            break
        got = harvest(src, cap, spec["match"], seen)
        picks.extend(got[: need - len(picks)])
        print(f"  harvest {src}: {len(got)} matches, picked {len(picks)}/{need}")
    if not picks:
        print(f"{coll}: nothing derivable (have={have})")
        return have, 0, 0
    now = datetime.now(timezone.utc).isoformat()
    ok = bad = 0
    for i in range(0, len(picks), 16):
        chunk = picks[i:i + 16]
        texts = [f"[{c} | {s}] {t.strip()[:4000]}" for c, _, t, _ in chunk]
        vecs = embed(texts, key)
        pts = []
        for (src, sid, t, pl), vec, txt in zip(chunk, vecs, texts):
            if len(vec) != 4096:
                print("DIM-MISMATCH", coll, len(vec))
                bad += 1
                continue
            payload = {
                "text": txt, "content": txt, "title": str(pl.get("title") or pl.get("name") or "")[:200],
                "corpus": "derived-v1", "ecosystem": spec["eco"], "created_at": now,
                "provenance": {"source_collection": src, "source_point_id": str(sid), "method": "derive-no-scrape"},
            }
            if isinstance(pl.get("tags"), list):
                payload["tags"] = pl["tags"][:12]
            pts.append({"id": str(uuid.uuid5(NS, f"{src}:{sid}")), "vector": vec, "payload": payload})
        if pts:
            http("PUT", f"{QD}/collections/{coll}/points?wait=true", {"points": pts})
            ok += len(pts)
    return have, ok, bad


TARGETS = {
    "solana_patterns_8b": {"min": 150, "eco": "solana", "match": SOL,
                            "srcs": [("defi_exploits_8b", 12000), ("known_scams_8b", 14000), ("security_patterns_8b", 9000)]},
    "solidity_patterns": {"min": 150, "eco": "evm", "match": EVM,
                          "srcs": [("contract_audits_8b", 1200), ("security_patterns_8b", 14000)]},
    "protocol_design_8b": {"min": 200, "eco": "any", "match": DESIGN,
                            "srcs": [("contract_audits_8b", 1200), ("security_patterns_8b", 14000)]},
}

if __name__ == "__main__":
    key = shim_key()
    for coll, spec in TARGETS.items():
        print(f"== {coll} (min {spec['min']})")
        have, ok, bad = build(coll, spec, key)
        print(f"RESULT {coll} have_before={have} inserted={ok} failed={bad}")
