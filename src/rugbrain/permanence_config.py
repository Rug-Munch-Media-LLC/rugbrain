"""Permanence storage configuration (B2 bucket + local cache paths).

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet) so the
snapshot/restore/ops modules can share one config without importing each
other.
"""

from __future__ import annotations

import os
from pathlib import Path

B2_KEY_ID = os.getenv("B2_APPLICATION_KEY_ID", "")
B2_APP_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_BUCKET = os.getenv("B2_BUCKET_NAME", "rmi-rag-permanence")

RAG_HF_MIRROR = os.getenv("RAG_HF_MIRROR", "false").lower() in ("true", "1", "yes")
HF_REPO = os.getenv("RAG_HF_REPO", "your-username/your-repo")
HF_TOKEN = os.getenv("HF_TOKEN", "")

LOCAL_CACHE = Path(os.getenv("RAG_LOCAL_CACHE", "/data/rag-storage"))
LOCAL_RETENTION_DAYS = 7
EMBEDDING_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "v1")

LOCAL_CACHE.mkdir(parents=True, exist_ok=True)
