"""Nightly B2 backup sync — Qdrant snapshots to Backblaze B2.

NAMING: "permanence" in this filename means the B2 BACKUP job, not a serving
tier. The code/RAG corpus is served from the server (hot set); B2 is backup/DR
only. Blockchain data is the one tier that is authoritative on B2.
See docs/DATA-PLACEMENT.md.

Exports canonical Qdrant collections as a columnar parquet file (default;
generated ``*_8b`` mirror collections are excluded by default)
or compressed NDJSON (legacy fallback) and uploads it to B2 under
``snapshots/YYYY-MM-DD/``:

    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.parquet       (default)
    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.jsonl.zst     (legacy)

Snapshots are written with zstd level 6 (was 19 — see note) and uploaded WITHOUT
client-side encryption: B2 server-side encryption (SSE-B2, AES256)
encrypts every object at rest for free. DuckDB can query parquet
directly from B2 over the S3-compatible API (no full download). Access
control is the private bucket + a scoped application key.

A ``manifest.json`` describing every collection (format, point count,
file size, sha256, timestamps, vector metadata) is uploaded alongside
the data and a ``snapshots/latest.json`` pointer records the most
recent date.

Data lifecycle this serves:
    Hot  (Qdrant): last 90 days, actively searched
    Cold (B2):     everything, forever, nightly snapshots
    Pruning:       documents older than 90 days are moved to B2 only
                   (kept in Qdrant if high severity) — see
                   ``src/rugbrain/qdrant_store.py::prune_old_documents``

Usage:
    python scripts/b2_permanence.py --dry-run
    python scripts/b2_permanence.py --collection threat_intel_v2 --limit 10
    python scripts/b2_permanence.py --format ndjson --limit 50
    python scripts/b2_permanence.py --env-file /path/to/b2-permanence.env
    python scripts/b2_permanence.py --prune              # retention dry-run
    python scripts/b2_permanence.py --prune --prune-live # actually prune

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY   (required)
    B2_BUCKET_NAME                              (default rmi-rag-permanence)
    B2_PREFER_TWIN=1 (default)                 prefer the _8b twin per collection, fall
                                            back to the base when no twin exists
    B2_CANONICAL_SUFFIX                        (default _8b)
    B2_EXCLUDE_SUFFIXES                         (legacy; only when B2_PREFER_TWIN=0)
    B2_PAYLOAD_ONLY_COLLECTIONS                 (default codebase_reference_v2; large vector corpus)
    B2_ENCRYPTION_KEY                           DEPRECATED — SSE-B2 encrypts
                                                at rest; kept only for
                                                reading/migrating legacy .enc
    QDRANT_URL / QDRANT_PORT                    Qdrant connection

See scripts/b2_common.py for the file format. Restore:
scripts/b2_restore.py. Convert legacy snapshots: scripts/migrate_to_parquet.py.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:  # works run directly or as scripts.*
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _parts_b2_permanence._h1 import *  # noqa: F403
from _parts_b2_permanence.p1 import *  # noqa: F403
from _parts_b2_permanence.p2 import *  # noqa: F403
from _parts_b2_permanence.p3 import *  # noqa: F403
from _parts_b2_permanence.p4 import *  # noqa: F403
from _parts_b2_permanence.p5 import *  # noqa: F403
from _parts_b2_permanence.p6 import *  # noqa: F403
from _parts_b2_permanence.p7 import *  # noqa: F403
from _parts_b2_permanence.p8 import *  # noqa: F403
from _parts_b2_permanence.p9 import *  # noqa: F403

if __name__ == "__main__":
    sys.exit(main())

