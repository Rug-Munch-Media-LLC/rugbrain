"""Ingest crypto scam / exploit / hack knowledge bases into RAG collections.

Pulls public, OSS, regularly-updated datasets and embeds them into Qdrant so
the RAG is an expert on known DeFi hacks, rug pulls, and crypto scams from
day one.

Target collections (one per knowledge type):

    defi_exploits          — specific incidents: how the hack was done, root
                             cause, PoC reproduction, funds lost.
                             Sources: DeFiHackLabs PoCs, rekt-news leaderboard.
    scam_patterns          — scam addresses / URLs / techniques: phishing
                             wallets, fake ICOs, trust-trading, rug-pull signs.
                             Sources: CryptoScamDB blacklist.
    crypto_vulnerabilities — weakness taxonomy + audit knowledge: smart
                             contract weakness classes, audit report findings,
                             attack-surface notes.
                             Sources: SlowMist Knowledge-Base, SWC Registry.

Each chunk carries canonical metadata (see rugbrain.crypto_schema):
source, date (ISO + date_ts), severity, protocol, chain, attack_vector,
amount_lost_usd, source_url — plus a short ``title``.

Pipeline guarantees:
  * Idempotent — deterministic UUIDv5 point IDs derived from source + content
    hash; re-running overwrites the same points instead of duplicating.
  * Checkpointed — progress is tracked in a JSON checkpoint file
    (``RAG_KNOWLEDGE_CHECKPOINT``, default /tmp/rag-knowledge/checkpoint.json)
    keyed by source → file → sha256, so already-ingested files are skipped.
  * Batched — embeddings are computed 50 at a time through
    (OpenAI-compatible /embeddings) and upserts are sent 50 points per call.

Usage:
    python scripts/ingest_scam_knowledge.py --dry-run
    python scripts/ingest_scam_knowledge.py --sources defihacklabs,rekt_news
    python scripts/ingest_scam_knowledge.py --sources all --limit 100
    python scripts/ingest_scam_knowledge.py --force   # ignore checkpoint

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with the embed
    model from RAG_EMBED_MODEL (default qwen3-embedding-8b, 2560-dim)
  - git, httpx, numpy, yaml (PyYAML)
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:  # works run directly or as scripts.*
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _parts_ingest_scam_knowledge.p1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p2 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p3 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p4 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p5 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p6 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p7 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p8 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p9 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p10 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F401,F403

if __name__ == "__main__":
    main()

