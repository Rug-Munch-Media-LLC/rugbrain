"""
RAG Permanence v2 - Backblaze B2 cold storage (native B2 API via b2sdk).
Hot (Redis) → Warm (local 7-day cache) → Cold (B2 permanent + HF mirror).

NAMING: "permanence" here means the B2 BACKUP job, not a serving tier. The
code/RAG corpus is served from the server (hot set); B2 is backup/DR only.
Blockchain data is the one tier that is authoritative on B2.
See docs/DATA-PLACEMENT.md.

Split into cohesive modules on 2026-10-25 (size ratchet); this module stays
the stable import surface, so ``from rugbrain.permanence import snapshot_all``
keeps working:

    permanence_config    bucket + local-cache paths
    permanence_b2        b2sdk put/get/list/delete (sync + async)
    permanence_hf        HuggingFace Storage Buckets mirror (opt-in)
    permanence_snapshot  snapshot_all
    permanence_restore   restore_all
    permanence_ops       nightly_cycle, b2_stats
"""

from __future__ import annotations

from rugbrain.permanence_config import (
    B2_APP_KEY,
    B2_BUCKET,
    B2_KEY_ID,
    EMBEDDING_VERSION,
    HF_REPO,
    HF_TOKEN,
    LOCAL_CACHE,
    LOCAL_RETENTION_DAYS,
    RAG_HF_MIRROR,
)
from rugbrain.permanence_ops import b2_stats, nightly_cycle
from rugbrain.permanence_restore import restore_all
from rugbrain.permanence_snapshot import snapshot_all

__all__ = [
    "B2_APP_KEY",
    "B2_BUCKET",
    "B2_KEY_ID",
    "EMBEDDING_VERSION",
    "HF_REPO",
    "HF_TOKEN",
    "LOCAL_CACHE",
    "LOCAL_RETENTION_DAYS",
    "RAG_HF_MIRROR",
    "b2_stats",
    "nightly_cycle",
    "restore_all",
    "snapshot_all",
]
