#!/usr/bin/env python3
"""Fetch GambleFi / crypto-gaming documentation into a deduped pages.jsonl snapshot.

Rules
    OFFICIAL first-party publishers only for the ``official`` tier (the operator's
    own docs, whitepaper, provably-fair page or repository); anything else is
    labelled ``community`` in ``source_kind`` and never mixed in silently. Public
    pages only — no login, no paywall, no captcha solving, no private group.
    ``robots.txt`` is fetched per host and honoured: a disallowed path is recorded
    as ``robots-denied`` and not fetched.

Cloudflare / JS pages
    Direct ``httpx`` first, then the local Pry MCP bridge (headless render) when the
    extracted text is too short to be the page. The method that produced the text is
    stored on every record, so a browser-only page is visible as such.

What is preserved
    Tokenomics, provably-fair construction, contract ABIs and fee/reward maths, with
    tables, formatting and code fences intact rather than flattened prose.

Evidence
    ``pages.jsonl`` is the snapshot; ``failures.jsonl`` is one line per source that
    produced no text (url, status, method). A crawl reporting "0 failures" without
    that file is a claim, not evidence.

Usage
    --dry-run probe without writing · --no-pry httpx only (fast audit) · default full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib import robotparser
from urllib.parse import urlsplit

import httpx
from gamblefi_sources import REPOS, SEEDS

UA: Final = "RugBrainDocsBot/1.0 (+https://rugmunch.io/bot; documentation archive)"
PRY_MCP_URL: Final = os.getenv("PRY_MCP_URL", "http://127.0.0.1:9098/mcp")
DEFAULT_OUT: Final = Path("data/gamblefi_docs/pages.jsonl")
MIN_TEXT: Final = 200
Meta = tuple[str, str, str, str, str, str]


def _record(meta: Meta, body: str, status: int, method: str) -> dict[str, object]:
    url, title, publisher, licence, doc_type, tier = meta
    return {
        "canonical_id": url.split("://", 1)[-1].removeprefix("www.").rstrip("/"),
        "url": url,
        "title": title,
        "section": doc_type,
        "publisher": publisher,
        "licence": licence,
        "source_kind": tier,
        "doc_type": doc_type,
        "fetch_method": method,
        "http_status": status,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "content": body,
    }


def _allowed(http: httpx.Client, url: str) -> bool:
    """True when ``url`` may be fetched. An unreachable robots.txt allows it."""
    parts = urlsplit(url)
    rp = robotparser.RobotFileParser()
    try:
        r = http.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10)
        if r.status_code != 200:
            return True
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def _markdown(text: str) -> str:
    """HTML -> markdown keeping tables, formatting and fenced code; else pass through."""
    if not text.lstrip().startswith(("<", "<!")):
        return text.strip()
    try:
        import trafilatura

        return (
            trafilatura.extract(
                text,
                output_format="markdown",
                include_tables=True,
                include_formatting=True,
                include_links=True,
                favor_recall=True,
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _pry(http: httpx.Client, url: str) -> str:
    """Render through the local Pry MCP bridge (SSE-framed, nested JSON result)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "pry_scrape",
            "arguments": {"url": url, "bypass_cloudflare": True, "js_render": True},
        },
    }
    try:
        r = http.post(
            PRY_MCP_URL,
            json=payload,
            timeout=float(os.getenv("PRY_TIMEOUT", "90")),
            headers={"Accept": "application/json, text/event-stream"},
        )
        if r.status_code != 200:
            return ""
        raw = r.text
        if "data:" in raw:
            raw = "".join(ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:"))
        body = json.loads(json.loads(raw)["result"]["content"][0]["text"])
        return str(body.get("data", {}).get("markdown", "")).strip()
    except Exception:
        return ""


def _fetch(http: httpx.Client, meta: Meta, *, allow_pry: bool) -> dict[str, object]:
    url = meta[0]
    if not _allowed(http, url):
        return _record(meta, "", 0, "robots-denied")
    try:
        r = http.get(url, timeout=45, follow_redirects=True)
    except Exception as exc:
        return _record(meta, "", 0, f"error:{type(exc).__name__}")
    body = _markdown(r.text if r.status_code == 200 else "")
    if len(body) >= MIN_TEXT:
        return _record(meta, body, r.status_code, "httpx+trafilatura")
    rendered = _pry(http, url) if allow_pry else ""
    if len(rendered) >= MIN_TEXT:
        return _record(meta, rendered, r.status_code or 200, "pry_scrape")
    return _record(meta, body, r.status_code, "empty")


def fetch(out: Path, *, dry_run: bool = False, allow_pry: bool = True) -> dict[str, int]:
    stats = {
        "sources": len(SEEDS) + len(REPOS),
        "ok": 0,
        "empty": 0,
        "denied": 0,
        "error": 0,
        "deduped": 0,
    }
    records: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    seen: set[str] = set()
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True) as http:
        for meta in (*SEEDS, *REPOS):
            rec = _fetch(http, meta, allow_pry=allow_pry)
            body = str(rec["content"])
            method = str(rec["fetch_method"])
            print(f"{rec['http_status']:>3} {method:<18} {len(body):>7} {meta[0]}", file=sys.stderr)
            if len(body) < MIN_TEXT:
                stats["denied" if method == "robots-denied" else "empty"] += 1
                failures.append(
                    {k: rec[k] for k in ("url", "title", "http_status", "fetch_method")}
                )
                continue
            if rec["sha256"] in seen:
                stats["deduped"] += 1
                continue
            seen.add(str(rec["sha256"]))
            stats["ok"] += 1
            records.append(rec)
    records.sort(key=lambda r: str(r["url"]))
    if not dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        (out.parent / "failures.jsonl").write_text(
            "".join(json.dumps(f, sort_keys=True) + "\n" for f in failures), encoding="utf-8"
        )
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pry", action="store_true", help="skip browser rendering")
    args = ap.parse_args()
    stats = fetch(args.out, dry_run=args.dry_run, allow_pry=not args.no_pry)
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
