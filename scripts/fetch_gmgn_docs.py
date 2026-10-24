#!/usr/bin/env python3
"""Fetch the GMGN documentation set into a deduped pages.jsonl snapshot.

Sources: GitBook's ``docs.gmgn.ai/index/llms.txt`` index (each page via its
``.md`` twin), ``gmgn.ai/ai`` and its skills blog post (Cloudflare, so via the
local Pry MCP bridge at ``PRY_MCP_URL``), and the docs of ``GMGNAI/gmgn-skills``
(tree API, falling back to ``gh_paths.txt`` when that anonymous quota runs out).
``robots.txt`` is captured with the pages so the crawl is auditable. Nothing
authenticates or bypasses a paywall. Output is deterministic: one record per
canonical URL, sorted, deduped by content hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

UA = "RugBrainDocsBot/1.0 (+https://rugmunch.io/bot; documentation archive)"
PRY_MCP_URL = os.getenv("PRY_MCP_URL", "http://127.0.0.1:9098/mcp")
GITHUB = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main"
TREE = "https://api.github.com/repos/GMGNAI/gmgn-skills/git/trees/main"
DOCS_INDEX = "https://docs.gmgn.ai/index/llms.txt"
DEFAULT_OUT = Path("data/gmgn_docs/pages.jsonl")
GH_PATHS = Path("data/gmgn_docs/gh_paths.txt")
EXTRA_PAGES = (("https://gmgn.ai/ai", "GMGN AI — on-chain skill hub", "skills-hub"),
               ("https://gmgn.ai/blog/gmgn-skills-for-ai-agents/", "GMGN Skills for AI Agents",
                "blog"))


def _record(
    url: str,
    title: str,
    section: str,
    body: str,
    *,
    publisher: str,
    licence: str,
    kind: str,
    status: int,
) -> dict[str, object]:
    return {
        "canonical_id": url.split("://", 1)[-1].removeprefix("www."),
        "url": url,
        "title": title,
        "section": section,
        "publisher": publisher,
        "licence": licence,
        "source_kind": kind,
        "http_status": status,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "content": body,
    }


def _title(body: str, fallback: str) -> str:
    return next((ln[2:].strip()[:200] for ln in body.splitlines() if ln.startswith("# ")), fallback)


def _pry_scrape(http: httpx.Client, url: str) -> tuple[int, str]:
    """Pry MCP over HTTP: SSE-framed, and the tool result is a nested JSON string."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "pry_scrape", "arguments": {"url": url, "bypass_cloudflare": True}},
    }
    r = http.post(
        PRY_MCP_URL,
        json=payload,
        timeout=180,
        headers={"Accept": "application/json, text/event-stream"},
    )
    if r.status_code != 200:
        return r.status_code, ""
    raw = r.text
    if "data:" in raw:
        raw = "".join(ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:"))
    try:
        body = json.loads(json.loads(raw)["result"]["content"][0]["text"])
        return 200, str(body.get("data", {}).get("markdown", ""))
    except (KeyError, IndexError, TypeError, ValueError):
        return 0, ""


def _github_paths(http: httpx.Client, stats: dict[str, int]) -> list[str]:
    try:
        tree = http.get(TREE, params={"recursive": "1"}, timeout=60).json().get("tree", [])
    except ValueError:
        tree = []
    paths = sorted(
        t["path"]
        for t in tree
        if t.get("type") == "blob" and t["path"].lower().endswith((".md", ".mdx", ".txt"))
    )
    if paths or not GH_PATHS.exists():
        return paths
    stats["github_paths_from_manifest"] = 1
    return sorted(p.strip() for p in GH_PATHS.read_text().splitlines() if p.strip())


def crawl(out: Path) -> dict[str, int]:
    records: dict[str, dict[str, object]] = {}
    stats = dict.fromkeys(
        ("pages", "failed", "duplicate", "github_paths_from_manifest"), 0)
    with httpx.Client(follow_redirects=True, headers={"User-Agent": UA}) as http:
        robots = http.get("https://docs.gmgn.ai/robots.txt", timeout=30)
        records["robots"] = _record(
            "https://docs.gmgn.ai/robots.txt",
            "robots.txt",
            "policy",
            robots.text,
            publisher="GMGN",
            licence="publisher-terms",
            kind="robots",
            status=robots.status_code,
        )
        index = http.get(DOCS_INDEX, timeout=60).text
        for url in sorted(u for ln in index.splitlines() if "docs.gmgn.ai" in ln
                          for u in [ln.split("(")[-1].split(")")[0]] if u.endswith(".md")):
            r = http.get(url, timeout=60)
            if r.status_code != 200:
                stats["failed"] += 1
                continue
            records[url] = _record(
                url,
                _title(r.text, url.rsplit("/", 1)[-1]),
                "agent-api",
                r.text,
                publisher="GMGN",
                licence="publisher-terms",
                kind="official-docs",
                status=r.status_code,
            )
        for url, title, section in EXTRA_PAGES:
            code, body = _pry_scrape(http, url)
            if not body:
                stats["failed"] += 1
                continue
            records[url] = _record(
                url,
                title,
                section,
                body,
                publisher="GMGN",
                licence="publisher-terms",
                kind="official-page",
                status=code,
            )
        for path in _github_paths(http, stats):
            url = f"{GITHUB}/{path}"
            r = http.get(url, timeout=60)
            if r.status_code != 200:
                stats["failed"] += 1
                continue
            records[url] = _record(
                url,
                _title(r.text, path),
                "skills-docs",
                r.text,
                publisher="GMGNAI/gmgn-skills",
                licence="see-repo-licence",
                kind="repo-docs",
                status=r.status_code,
            )

    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for key in sorted(records):
        row = records[key]
        if row["sha256"] in seen:
            stats["duplicate"] += 1
            continue
        seen.add(str(row["sha256"]))
        rows.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    stats["pages"] = len(rows)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    print(json.dumps(crawl(args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
