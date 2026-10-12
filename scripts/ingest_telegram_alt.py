#!/usr/bin/env python3
"""CLI: mirror @cryptorugmunch's Telegram channels into RugBrain (second account).

Usage:
    python scripts/ingest_telegram_alt.py --channel cointelegraph --dry-run
    python scripts/ingest_telegram_alt.py --check-credentials
    python scripts/ingest_telegram_alt.py --limit-per-channel 25 --verify

The channel list is :data:`MIRROR_CHANNELS`
— the pinned mirror of the bot's ``_DEFAULT_CHANNELS``.

The ingest itself lives in ``rugbrain.ingest_sources.alt_ingest`` so this
file stays a thin argument/reporting surface.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_sources.alt_ingest_channels import ALT_INGEST_CHANNELS as MIRROR_CHANNELS
from rugbrain.ingest_sources.alt_ingest import (
    LIMIT_PER_CHANNEL,
    AltIngestCredentialError as AltIngestCredentialError,
    alt_ingest_credentials_present as mirror_credentials_present,
    run_mirror,
)


async def _rerank_verify(sample: str) -> float | None:
    """Rerank one ingested item through the hosted Qwen3-Reranker-8B chain."""
    try:
        from rugbrain.rerank_lanes import hosted_rerank

        results = await hosted_rerank("bitcoin ethereum crypto news", [sample[:2000]])
        if results:
            return float(results[0].get("relevance_score", 0.0))
    except Exception as exc:  # a probe failure must never fail the sweep
        print(f"  rerank-verify failed: {exc}")
    return None


async def _run(args: argparse.Namespace) -> dict[str, object]:
    summary = await run_mirror(
        args.channel,
        limit_per_channel=args.limit_per_channel,
        dry_run=args.dry_run,
    )
    if args.verify and not args.dry_run and summary.get("verify_sample"):
        score = await _rerank_verify(summary["verify_sample"])
        summary["rerank_score"] = score
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest @cryptorugmunch's Telegram channels into RugBrain (alt account; main account posts)"
    )
    parser.add_argument(
        "--channel", action="append", default=None, help="mirror only this handle (repeatable)"
    )
    parser.add_argument(
        "--limit-per-channel",
        type=int,
        default=LIMIT_PER_CHANNEL,
        help="max messages fetched per channel",
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch + count, embed nothing")
    parser.add_argument("--verify", action="store_true", help="rerank-verify an ingested item")
    parser.add_argument(
        "--check-credentials",
        action="store_true",
        help="only report whether the second account's credentials are present",
    )
    args = parser.parse_args()

    if args.check_credentials:
        ok, missing = mirror_credentials_present()
        if ok:
            print("mirror credentials: PRESENT")
            sys.exit(0)
        print(f"mirror credentials: MISSING -> {', '.join(missing)}")
        sys.exit(2)

    try:
        summary = asyncio.run(_run(args))
    except AltIngestCredentialError as exc:
        print(f"[BLOCKED] {exc}")
        sys.exit(3)

    print("=" * 62)
    print(
        f"TELEGRAM MIRROR channels={summary['channels_fetched']}/{summary['channels']} "
        f"(list={len(MIRROR_CHANNELS)})"
    )
    print(f"  messages seen:                  {summary['messages_seen']}")
    print(f"  skipped (media-only / short):    {summary['messages_skipped_noise']}")
    print(f"  new candidates:                 {summary['candidates']}")
    print(f"  skipped duplicate (channel,msg): {summary['duplicate_msgid']}")
    print(f"  skipped duplicate (content hash):{summary['duplicate_hash']}")
    print(f"  inserted:                       {summary['inserted']}")
    print(f"  failed:                         {summary['failed']}")
    if summary.get("rerank_score") is not None:
        print(f"  rerank-verified score:          {summary['rerank_score']}")
    for err in summary["errors"]:
        print(f"  ERROR {err}")
    print("TARGET: news_v2 -> news_v2_8b (4096-dim, Qwen3-Embedding-8B)")
    print("=" * 62)


if __name__ == "__main__":
    main()
