"""Build the verified X source registry: inventory -> verify -> classify -> report."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from xsources.classify import verdict  # noqa: E402
from xsources.inventory import collect  # noqa: E402
from xsources.verify import verify_all  # noqa: E402

SEED_PATH = ROOT / "config" / "x_sources_seed.json"
OUT = ROOT / "data" / "x_sources.json"
REJECTED = ROOT / "data" / "x_sources_rejected.json"
REPORT = ROOT / "data" / "x_sources_report.md"


def _report(
    verified: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    inv: dict[str, Any],
    policy: dict[str, Any],
) -> str:
    by_cat = Counter(row["category"] for row in verified)
    squats = [r for r in rejected if r["category"] == "squat"]
    lines = [
        "# X source registry — build report",
        "",
        f"Inventory: {inv['raw_records']} records -> {inv['unique_handles']} unique handles "
        f"(case-insensitive). Verified: {len(verified)}. Rejected: {len(rejected)} "
        f"(squat/impersonation: {len(squats)}).",
        "",
        "## Counts by category",
        "",
        "| category | count | trust |",
        "| --- | ---: | ---: |",
    ]
    for cat, count in by_cat.most_common():
        lines.append(f"| {cat} | {count} | {policy['category_trust'].get(cat, 0)} |")
    lines += ["", "## Rejections (squat / impersonation)", ""]
    for row in squats:
        lines.append(f"- @{row['handle']} [{row['reason']}] {row['evidence']}")
    lines += ["", "## Rejections (other)", ""]
    for row in rejected:
        if row["category"] != "squat":
            lines.append(f"- @{row['handle']} [{row['reason']}] {row['evidence']}")
    lines += ["", "## Samples (3 per category)", ""]
    for cat in by_cat:
        rows = [r for r in verified if r["category"] == cat][:3]
        lines.append(f"**{cat}**")
        for row in rows:
            lines.append(
                f"- @{row['handle']} — {row['name']} — {row['followers']} followers "
                f"— trust {row['trust']} — {row['evidence_url']}"
            )
        lines.append("")
    lines += ["## Highest-value news sources", ""]
    news = sorted(
        (r for r in verified if r["category"] in ("news_primary", "security", "aggregator")),
        key=lambda r: (-r["trust"], -r["followers"]),
    )[:12]
    for row in news:
        lines.append(f"- @{row['handle']} ({row['category']}, {row['followers']} followers)")
    return "\n".join(lines) + "\n"


async def main() -> int:
    seed = json.loads(SEED_PATH.read_text())
    policy = seed["policy"]
    items, inv = collect(seed)
    probe_items = [
        {
            "handle": h,
            "name": None,
            "category": "squat",
            "site": None,
            "provenance": ["config/x_sources_seed.json:squat_probes"],
        }
        for h in seed.get("squat_probes", [])
        if h.lower() not in {i["handle"].lower() for i in items}
    ]
    verified_items = await verify_all(items)
    probe_results = await verify_all(probe_items)
    probes = {h.lower() for h in seed.get("squat_probes", [])}
    renames = {k.lower(): v for k, v in seed.get("renames", {}).items()}
    probe_targets = seed.get("probe_targets", {})

    rows = [verdict(item, policy, probes, renames, probe_targets) for item in verified_items]
    rows += [verdict(item, policy, probes, renames, probe_targets) for item in probe_results]
    verified = sorted(
        [r for r in rows if r["status"] == "verified"],
        key=lambda r: (r["category"], -r["trust"], -r["followers"]),
    )
    rejected = [r for r in rows if r["status"] != "verified"]

    counts = Counter(r["category"] for r in verified)
    payload = {
        "version": 1,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "method": (
            "Handle inventory from company_channels.json + rmi-backend x_intel config/CT_ACCOUNTS "
            "+ curated seed; each handle resolved via https://api.fxtwitter.com/<handle> "
            "(screen_name, followers, snowflake-derived created_at); 404 retried once after 1.5s "
            "then case variants; official site probed where known; squat = probe hit, "
            "screen_name mismatch on entity-style name, or new account claiming an established entity."
        ),
        "policy": policy,
        "counts": {
            "verified": len(verified),
            "rejected": len(rejected),
            "squat_rejected": sum(1 for r in rejected if r["category"] == "squat"),
            "by_category": dict(counts),
            "inventory": inv,
        },
        "sources": verified,
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    REJECTED.write_text(
        json.dumps({"generated_at": payload["generated_at"], "rejected": rejected}, indent=1) + "\n"
    )
    REPORT.write_text(_report(verified, rejected, inv, policy))
    print(json.dumps(payload["counts"], indent=1))
    return 0 if len(verified) >= 150 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
