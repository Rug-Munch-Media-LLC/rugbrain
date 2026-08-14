#!/usr/bin/env python3
"""Unified RugBrain ingestion CLI — one script to rule them all.

Consolidates the seven legacy ingestion scripts into a single entry point
with subcommands:

    python scripts/ingest.py scams --sources all
    python scripts/ingest.py coding --lang solidity
    python scripts/ingest.py security --patterns
    python scripts/ingest.py all

Each subcommand delegates to the pre-existing main_async() functions of the
original scripts, preserving all behaviour, idempotency guarantees, and
metadata conventions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ingest")


# ── Subcommand handlers ──────────────────────────────────────────────────


def _scam_sources(sources_arg: str) -> list[str]:
    """Resolve --sources argument (comma-separated or 'all')."""
    from ingest_scam_knowledge import SOURCE_COLLECTIONS

    if sources_arg.strip().lower() == "all":
        return list(SOURCE_COLLECTIONS.keys())
    sources = [s.strip() for s in sources_arg.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCE_COLLECTIONS]
    if unknown:
        log.error("unknown sources: %s", ", ".join(unknown))
        valid = ", ".join(SOURCE_COLLECTIONS.keys())
        log.error("valid sources: %s", valid)
        sys.exit(2)
    return sources


def cmd_scams(args: argparse.Namespace) -> None:
    """Ingest scam / exploit / hack knowledge bases."""
    from ingest_scam_knowledge import main_async

    sources = _scam_sources(args.sources)

    results = asyncio.run(
        main_async(
            sources=sources,
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force,
            fetch_rekt=not args.no_rekt,
            dedup=not args.no_dedup,
            dedup_threshold=args.dedup_threshold,
            priority_only=args.priority_only,
            refresh=args.refresh,
        )
    )
    _print_scam_summary(results)


def _print_scam_summary(results: dict[str, dict[str, Any]]) -> None:
    from ingest_scam_knowledge import SOURCE_COLLECTIONS

    print("=" * 70)
    print("SCAM KNOWLEDGE INGEST SUMMARY")
    print("=" * 70)
    for source, stats in results.items():
        print(
            f"  {source:<22} collection={SOURCE_COLLECTIONS.get(source, '?'):<22} "
            f"upserted={stats.get('upserted', 0)} skipped={stats.get('skipped', 0)} "
            f"dedup={stats.get('dedup_skipped', 0)} chunks={stats.get('chunks', 0)}"
        )
        if stats.get("last_updated"):
            print(f"    last_updated: {stats['last_updated']}")
        if stats.get("error"):
            print(f"    ERROR: {stats['error']}")
    print("=" * 70)


def _run_coding_single(
    lang: str, dry_run: bool, no_network: bool, source_filter: str | None
) -> None:
    """Run a single coding knowledge ingester by language."""
    lang_map: dict[str, tuple[str, str]] = {
        "solidity": ("ingest_solidity_knowledge", "solidity"),
        "rust": ("ingest_rust_solana_knowledge", "solana"),
        "solana": ("ingest_rust_solana_knowledge", "solana"),
        "move": ("ingest_alt_vm_knowledge", "alt-vm"),
        "cairo": ("ingest_alt_vm_knowledge", "alt-vm"),
    }
    if lang not in lang_map:
        log.error("unknown language: %s (choose: solidity, rust, move, cairo)", lang)
        sys.exit(2)

    mod_name, label = lang_map[lang]
    mod = __import__(mod_name)

    # Build a minimal Namespace matching what each script's main_async expects.
    args = argparse.Namespace(
        dry_run=dry_run,
        no_fetch=no_network,
        no_clone=no_network,
        source=source_filter,
    )
    stats = asyncio.run(mod.main_async(args))
    print("=" * 60)
    print(f"{label.upper()} CODING KNOWLEDGE INGEST COMPLETE")
    for k, v in (stats if isinstance(stats, dict) else {"items": len(stats)}).items():
        print(f"  {k}: {v}")
    print("=" * 60)


def cmd_coding(args: argparse.Namespace) -> None:
    """Ingest coding knowledge (Solidity / Rust-Solana / Move-Cairo)."""
    if args.lang == "all":
        for lang in ("solidity", "rust", "move", "cairo"):
            log.info("--- coding ingest: %s ---", lang)
            try:
                _run_coding_single(
                    lang, dry_run=args.dry_run, no_network=args.no_network, source_filter=None
                )
            except Exception as e:
                log.error("coding_ingest_failed lang=%s err=%s", lang, str(e)[:200])
    else:
        _run_coding_single(
            args.lang,
            dry_run=args.dry_run,
            no_network=args.no_network,
            source_filter=None,
        )


def cmd_security(args: argparse.Namespace) -> None:
    """Ingest security patterns + audit findings + contract codebases."""
    ran = False

    if args.patterns or args.all:
        from ingest_security_patterns import main_async

        ns = argparse.Namespace(dry_run=args.dry_run, limit=args.limit)
        totals = asyncio.run(main_async(ns))
        print("=" * 60)
        print("SECURITY PATTERN INGEST COMPLETE")
        for col, n in totals.items():
            print(f"  {col}: {n}")
        print("=" * 60)
        ran = True

    if args.codebase or args.all:
        from ingest_codebase_knowledge import DEFAULT_MAX_CHUNKS, DEFAULT_MAX_CONTRACTS, main_async

        ns = argparse.Namespace(
            dry_run=args.dry_run,
            protocol=args.protocol,
            max_contracts=args.max_contracts or DEFAULT_MAX_CONTRACTS,
            max_chunks=args.max_chunks or DEFAULT_MAX_CHUNKS,
            no_clone=args.no_clone,
            repo_dir=args.repo_dir or os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases"),  # noqa: S108  # nosec B108
        )
        all_stats = asyncio.run(main_async(ns))
        print("=" * 60)
        print("CODEBASE KNOWLEDGE INGEST COMPLETE")
        for s in all_stats:
            print(
                f"  {s['id']:<12} files={s['files']:<4} chunks={s['chunks']:<5} "
                f"inserted={s['inserted']} skipped={s['skipped']}"
            )
        print("=" * 60)
        ran = True

    if not ran:
        log.warning("no security mode selected; use --patterns, --codebase, or --all")


def cmd_continuous(args: argparse.Namespace) -> None:
    """Run the continuous ingestion scheduler (service or one-shot)."""
    from ingest_continuous import main

    # Pass through by remapping sys.argv so the continuous script parses its own args.
    old_argv = sys.argv[:]
    try:
        new_argv = [sys.argv[0]]
        if args.continuous_once:
            new_argv.append("--once")
        if args.continuous_job:
            new_argv.extend(["--job", args.continuous_job])
        if args.continuous_force:
            new_argv.append("--force")
        if args.dry_run:
            new_argv.append("--dry-run")
        sys.argv = new_argv
        main()
    finally:
        sys.argv = old_argv


def cmd_all(args: argparse.Namespace) -> None:
    """Run all batch ingestion pipelines once."""
    # Ensure sub-commands see the right flags
    args.patterns = True
    args.codebase = True
    args.all = True
    log.info("=== RUNNING ALL INGESTION PIPELINES ===")

    # 1. Scams
    log.info("--- scams ---")
    try:
        cmd_scams(args)
    except Exception as e:
        log.error("scams ingest failed: %s", e)

    # 2. Coding (all languages)
    log.info("--- coding ---")
    try:
        cmd_coding(args)
    except Exception as e:
        log.error("coding ingest failed: %s", e)

    # 3. Security (patterns + codebase)
    log.info("--- security ---")
    try:
        cmd_security(args)
    except Exception as e:
        log.error("security ingest failed: %s", e)

    log.info("=== ALL PIPELINES COMPLETE ===")


# ── CLI ──────────────────────────────────────────────────────────────────


def _add_scams_args(sub: argparse.ArgumentParser) -> None:
    from ingest_scam_knowledge import DEDUP_THRESHOLD_DEFAULT

    sub.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources or 'all' (defihacklabs, rekt_news, etc.)",
    )
    sub.add_argument("--limit", type=int, default=None, help="Max docs per source")
    sub.add_argument("--dry-run", action="store_true", help="Preview without embedding/upsert")
    sub.add_argument(
        "--force", action="store_true", help="Ignore checkpoint (re-ingest everything)"
    )
    sub.add_argument("--no-rekt", action="store_true", help="Skip fetching rekt.news leaderboard")
    sub.add_argument("--no-dedup", action="store_true", help="Disable chunk deduplication")
    sub.add_argument(
        "--dedup-threshold",
        type=float,
        default=DEDUP_THRESHOLD_DEFAULT,
        help=f"Vector similarity threshold for dedup (default {DEDUP_THRESHOLD_DEFAULT})",
    )
    sub.add_argument(
        "--priority-only",
        metavar="SEVERITY",
        default=None,
        help="Only ingest chunks at/above SEVERITY",
    )
    sub.add_argument("--refresh", action="store_true", help="git pull cloned sources")


def _add_coding_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--lang",
        default="all",
        choices=["solidity", "rust", "solana", "move", "cairo", "all"],
        help="Target language (default: all)",
    )
    sub.add_argument("--dry-run", action="store_true", help="Preview counts only")
    sub.add_argument(
        "--no-network",
        action="store_true",
        help="Curated content only (no fetch/clone from network)",
    )


def _add_security_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--dry-run", action="store_true", help="Preview counts only")
    sub.add_argument(
        "--patterns", action="store_true", help="Ingest security patterns + audit findings"
    )
    sub.add_argument("--codebase", action="store_true", help="Ingest contract codebases")
    sub.add_argument("--all", action="store_true", help="Run both patterns and codebase")
    sub.add_argument("--limit", type=int, default=None, help="Max docs per collection (patterns)")
    sub.add_argument("--protocol", default=None, help="Only ingest this protocol id (codebase)")
    sub.add_argument(
        "--max-contracts", type=int, default=None, help="Max files per protocol (codebase)"
    )
    sub.add_argument("--max-chunks", type=int, default=None, help="Global chunk cap (codebase)")
    sub.add_argument(
        "--no-clone", action="store_true", help="Use existing repos, skip cloning (codebase)"
    )
    sub.add_argument(
        "--repo-dir",
        default=None,
        help="Directory for cloned repos (codebase, default: RAG_CODEBASE_DIR env var)",
    )


def _add_all_args(sub: argparse.ArgumentParser) -> None:
    """Common args used when running 'all' pipelines — shared across submodes."""
    sub.add_argument("--dry-run", action="store_true", help="Preview without embedding/upsert")
    sub.add_argument("--limit", type=int, default=None, help="Max docs per source")
    sub.add_argument("--force", action="store_true", help="Ignore checkpoint")
    sub.add_argument("--no-network", action="store_true", help="Skip network fetch/clone")
    sub.add_argument("--refresh", action="store_true", help="git pull cloned sources")
    # Coding-specific
    sub.add_argument(
        "--lang",
        default="all",
        choices=["solidity", "rust", "solana", "move", "cairo", "all"],
        help="Target language for coding ingest (default: all)",
    )
    # Security-specific
    sub.add_argument("--protocol", default=None, help="Only ingest this protocol id")
    sub.add_argument("--max-contracts", type=int, default=None)
    sub.add_argument("--max-chunks", type=int, default=None)
    sub.add_argument("--no-clone", action="store_true")
    sub.add_argument("--repo-dir", default=None)
    # Scams-specific (set sane defaults)
    sub.add_argument("--sources", default="all")
    sub.add_argument("--no-rekt", action="store_true")
    sub.add_argument("--no-dedup", action="store_true")
    sub.add_argument("--dedup-threshold", type=float, default=0.95)
    sub.add_argument("--priority-only", default=None)
    # Continuous-specific
    sub.add_argument("--once", dest="continuous_once", action="store_true")


def _add_continuous_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--once", dest="continuous_once", action="store_true", help="Run due jobs once and exit"
    )
    sub.add_argument("--job", dest="continuous_job", default=None, help="Run only this job")
    sub.add_argument(
        "--force", dest="continuous_force", action="store_true", help="Run regardless of checkpoint"
    )
    sub.add_argument("--dry-run", action="store_true", help="Fetch only, don't store")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified RugBrain ingestion — batch & continuous pipelines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python scripts/ingest.py scams --sources all --dry-run
  python scripts/ingest.py coding --lang solidity
  python scripts/ingest.py security --patterns
  python scripts/ingest.py all
  python scripts/ingest.py continuous --once""",
    )
    sub = parser.add_subparsers(dest="command", help="Ingestion mode")

    scams = sub.add_parser("scams", help="Ingest scam/exploit/hack knowledge bases")
    _add_scams_args(scams)

    coding = sub.add_parser("coding", help="Ingest coding knowledge (Solidity/Rust/Move/Cairo)")
    _add_coding_args(coding)

    security = sub.add_parser("security", help="Ingest security patterns + contract codebases")
    _add_security_args(security)

    all_ = sub.add_parser("all", help="Run all batch pipelines once")
    _add_all_args(all_)

    continuous = sub.add_parser("continuous", help="Run the continuous ingestion scheduler")
    _add_continuous_args(continuous)

    args = parser.parse_args()

    if args.command == "scams":
        cmd_scams(args)
    elif args.command == "coding":
        cmd_coding(args)
    elif args.command == "security":
        cmd_security(args)
    elif args.command == "all":
        cmd_all(args)
    elif args.command == "continuous":
        cmd_continuous(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
