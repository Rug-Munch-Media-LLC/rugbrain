#!/usr/bin/env python3
"""One registry for every documentation set we hold (Qdrant-backed)."""
import argparse
import json
import re
import sys
from urllib.parse import urlparse

import docs_registry_io as io
import docs_registry_qdrant as qd
import docs_registry_quality as quality

COLLECTION = "codebase_reference_v2_8b"
PERMISSIVE = ("MIT", "APACHE", "BSD", "CC-BY", "CC0", "PUBLIC-DOMAIN", "UNLICENSE")


def _source_repo(url):
    host = urlparse(url or "").netloc.lower()
    path = urlparse(url or "").path.strip("/")
    if "github.com" in host and path:
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[0] + "/" + re.sub(r"\.git$", "", parts[1])
    return urlparse(url or "").netloc or None


def build(collection=COLLECTION):
    freshness = io.load_freshness()
    counts = qd.facets(collection)
    entries = []
    for lib in sorted(counts):
        if not lib.startswith("docs:"):
            continue
        points = qd.scroll(collection, lib)
        if not points:
            continue
        first = points[0]
        urls, seen = [], set()
        for point in points:
            path = point.get("file_path") or ""
            if path and path not in seen:
                seen.add(path)
                urls.append(path)
        verdict = quality.summarise(urls)
        licence = (first.get("license") or "").strip()
        seed = freshness.get(lib, {})
        entries.append({
            "library_id": lib,
            "display_name": first.get("library_name") or seed.get("title") or lib,
            "source_repo": _source_repo(first.get("source_url") or seed.get("source_url")),
            "source_url": first.get("source_url") or seed.get("source_url"),
            "licence": licence or "UNKNOWN",
            "version": first.get("source_commit") or None,
            "page_count_estimate": len(urls),
            "chunk_count": counts[lib],
            "fetched_at": seed.get("fetched_at"),
            "publishable": licence.upper().startswith(PERMISSIVE),
            "quality": verdict["quality"],
            "landing_ratio": verdict["landing_ratio"],
            "sampled_pages": verdict["sampled_pages"],
            "collection": collection,
        })
    return entries


def cmd_build(args):
    data = io.load()
    built = {e["library_id"]: e for e in build()}
    keep = {e["library_id"]: e for e in data["entries"]
            if e["library_id"] not in built and e.get("collection") != COLLECTION}
    merged = list(built.values()) + list(keep.values())
    saved = io.save({"source": "Qdrant " + COLLECTION, "entries": merged})
    print("built %d entries (%d new, %d kept)" %
          (saved["count"], len(built), len(keep)))
    return 0


def cmd_list(args):
    entries = io.load()["entries"]
    if args.quality:
        entries = [e for e in entries if e.get("quality") == args.quality]
    for entry in entries:
        print("%-38s %-8s %7d chunks  %3.0f%% landing  %s" % (
            entry["library_id"], entry.get("quality"), entry.get("chunk_count", 0),
            100 * float(entry.get("landing_ratio") or 0), entry.get("licence")))
    print("total=%d" % len(entries))
    return 0


def cmd_search(args):
    for entry in io.search(io.load()["entries"], args.query):
        print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


def cmd_add(args):
    data = io.load()
    entry = json.loads(open(args.json_file, encoding="utf-8").read())
    io.save({"source": data.get("source", ""),
             "entries": [e for e in data["entries"]
                         if e["library_id"] != entry["library_id"]] + [entry]})
    print("added " + entry["library_id"])
    return 0


def cmd_validate(args):
    entries = io.load()["entries"]
    fails, debt = [], {}
    for entry in entries:
        errs = quality.validate(entry)
        if errs:
            fails.append((entry["library_id"], errs))
        if entry.get("quality") == "landing":
            debt[entry["library_id"]] = "landing"
    for lib, errs in fails:
        print("FAIL %-38s %s" % (lib, "; ".join(errs)))
    print("validated=%d failing=%d landing_debt=%d" %
          (len(entries), len(fails), len(debt)))
    return 1 if (fails and not args.report_only) else 0


def cmd_refresh_status(args):
    entries = io.load()["entries"]
    freshness = io.load_freshness()
    stale = []
    for entry in entries:
        seed = freshness.get(entry["library_id"])
        if seed:
            entry["fetched_at"] = seed.get("fetched_at")
        if not entry.get("fetched_at"):
            stale.append(entry["library_id"])
    io.save({"source": "Qdrant " + COLLECTION, "entries": entries})
    print("freshness known=%d unknown=%d" % (len(entries) - len(stale), len(stale)))
    for lib in stale:
        print("NO-FETCHED-AT " + lib)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="docs_registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(func=cmd_build)
    p_list = sub.add_parser("list")
    p_list.add_argument("--quality")
    p_list.set_defaults(func=cmd_list)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.set_defaults(func=cmd_search)
    p_add = sub.add_parser("add")
    p_add.add_argument("json_file")
    p_add.set_defaults(func=cmd_add)
    p_val = sub.add_parser("validate")
    p_val.add_argument("--report-only", action="store_true")
    p_val.set_defaults(func=cmd_validate)
    sub.add_parser("refresh-status").set_defaults(func=cmd_refresh_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    sys.exit(main())
