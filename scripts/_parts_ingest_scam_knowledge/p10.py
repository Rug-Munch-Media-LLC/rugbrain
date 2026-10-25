"""Part 10/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p2 import Checkpoint
from _parts_ingest_scam_knowledge.p9 import _ingest_source

async def main_async(
    sources: list[str],
    limit: int | None,
    dry_run: bool,
    force: bool,
    fetch_rekt: bool,
    dedup: bool = True,
    dedup_threshold: float = DEDUP_THRESHOLD_DEFAULT,
    priority_only: str | None = None,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    checkpoint = Checkpoint(CHECKPOINT_PATH)
    checkpoint.load()
    # One-time filesystem setup before the hot loop; blocking is fine here.
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    results: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=120.0) as client:
        for source in sources:
            try:
                upserted, skipped, total, dedup_skipped = await _ingest_source(
                    source,
                    client,
                    checkpoint,
                    limit,
                    dry_run,
                    force,
                    fetch_rekt,
                    dedup=dedup,
                    dedup_threshold=dedup_threshold,
                    priority_only=priority_only,
                    refresh=refresh,
                )
                results[source] = {
                    "upserted": upserted,
                    "skipped": skipped,
                    "chunks": total,
                    "dedup_skipped": dedup_skipped,
                    "last_updated": checkpoint.get_last_updated(source),
                }
            except Exception as e:
                logger.error("source=%s failed: %s", source, str(e)[:300])
                results[source] = {
                    "upserted": 0,
                    "skipped": 0,
                    "chunks": 0,
                    "dedup_skipped": 0,
                    "error": str(e)[:300],
                }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest scam/exploit knowledge bases into RAG")
    parser.add_argument(
        "--sources",
        default=(
            "defihacklabs,rekt_news,cryptoscamdb,slowmist,swc_registry,"
            "defillama_hacks,web3hacks,immunefi,openzeppelin_audits,postmortems"
        ),
        help="Comma-separated sources (all | defihacklabs,rekt_news,cryptoscamdb,"
        "slowmist,swc_registry,defillama_hacks,web3hacks,immunefi,"
        "openzeppelin_audits,postmortems)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max docs per source")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview counts without embedding/upsert"
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore checkpoint (re-ingest everything)"
    )
    parser.add_argument(
        "--no-rekt", action="store_true", help="Skip fetching rekt.news leaderboard (network)"
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable chunk deduplication against existing Qdrant content",
    )
    parser.add_argument(
        "--dedup-threshold",
        type=float,
        default=DEDUP_THRESHOLD_DEFAULT,
        help=f"Vector similarity above which a chunk is considered a duplicate "
        f"(default {DEDUP_THRESHOLD_DEFAULT})",
    )
    parser.add_argument(
        "--priority-only",
        type=str,
        default=None,
        metavar="SEVERITY",
        help="Only ingest chunks at or above SEVERITY (critical|high|medium|low|info|unknown)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="git pull cloned sources before ingesting (freshness)",
    )
    args = parser.parse_args()

    if args.sources.strip().lower() == "all":
        sources = list(SOURCE_COLLECTIONS.keys())
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCE_COLLECTIONS]
    if unknown:
        parser.error(f"unknown sources: {', '.join(unknown)}")

    results = asyncio.run(
        main_async(
            sources,
            args.limit,
            args.dry_run,
            args.force,
            not args.no_rekt,
            dedup=not args.no_dedup,
            dedup_threshold=args.dedup_threshold,
            priority_only=args.priority_only,
            refresh=args.refresh,
        )
    )

    print("=" * 70)
    print("SCAM KNOWLEDGE INGEST SUMMARY")
    print("=" * 70)
    for source, stats in results.items():
        print(
            f"  {source:<22} collection={SOURCE_COLLECTIONS[source]:<22} "
            f"upserted={stats.get('upserted', 0)} skipped={stats.get('skipped', 0)} "
            f"dedup={stats.get('dedup_skipped', 0)} chunks={stats.get('chunks', 0)}"
        )
        if stats.get("last_updated"):
            print(f"    last_updated: {stats['last_updated']}")
        if stats.get("error"):
            print(f"    ERROR: {stats['error']}")
    print("=" * 70)


__all__ = [
    "main_async",
    "main",
]
