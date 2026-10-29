"""Header chunk 1/1 of scripts/b2_permanence.py (imports + constants)."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import qdrant_client
import qdrant_client.http.exceptions
from b2sdk.v2 import B2Api

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common
from b2_common import (
    COMPRESSED_SUFFIX,
    NDJSON_SUFFIX,
    PARQUET_SUFFIX,
    compress_file,
    download_file,
    ensure_bucket,
    env_or,
    get_b2_api,
    load_env_file,
    sha256_file,
    stream_parquet,
    upload_file,
)
from permanence_chunk_plan import estimate_minutes, plan_chunks
from permanence_chunk_run import run_chunk
from permanence_scope import daily_scope
from permanence_shard import export_parquet_shards
from permanence_shard_contract import pin_expected_shards
from permanence_shard_resume import entry_is_done, resume_state
from permanence_skip import carry_if_unchanged, record_export

logger = logging.getLogger("b2_permanence")

DEFAULT_QDRANT_HOST = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

DEFAULT_QDRANT_PORT = os.getenv("QDRANT_PORT", "6334")

MANIFEST_NAME = "manifest.json"

LATEST_NAME = "latest.json"

DEFAULT_EXCLUDED_SUFFIXES = ("_8b",)

DEFAULT_PAYLOAD_ONLY_COLLECTIONS = ("codebase_reference_v2",)

ZSTD_LEVEL = int(os.environ.get("CC_ZSTD_LEVEL", "6"))

SNAPSHOT_PREFIX = "snapshots"

MIN_ESTIMATE_S = 20.0

SCROLL_BATCH = 400

MANIFEST_SCHEMA_VERSION = 2

SNAPSHOT_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

RETAIN_DAILY = 30

RETAIN_WEEKLY = 4

RETAIN_MONTHLY = 12

MIN_RETAIN_FLOOR = 1


__all__ = [
    "COMPRESSED_SUFFIX",
    "DEFAULT_EXCLUDED_SUFFIXES",
    "DEFAULT_PAYLOAD_ONLY_COLLECTIONS",
    "DEFAULT_QDRANT_HOST",
    "DEFAULT_QDRANT_PORT",
    "LATEST_NAME",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "MIN_ESTIMATE_S",
    "MIN_RETAIN_FLOOR",
    "NDJSON_SUFFIX",
    "PARQUET_SUFFIX",
    "RETAIN_DAILY",
    "RETAIN_MONTHLY",
    "RETAIN_WEEKLY",
    "SCROLL_BATCH",
    "SNAPSHOT_DATE_RE",
    "SNAPSHOT_PREFIX",
    "UTC",
    "ZSTD_LEVEL",
    "Any",
    "B2Api",
    "Iterable",
    "Path",
    "annotations",
    "argparse",
    "b2_common",
    "carry_if_unchanged",
    "compress_file",
    "daily_scope",
    "datetime",
    "download_file",
    "ensure_bucket",
    "entry_is_done",
    "env_or",
    "estimate_minutes",
    "export_parquet_shards",
    "get_b2_api",
    "json",
    "load_env_file",
    "logger",
    "logging",
    "math",
    "os",
    "pin_expected_shards",
    "plan_chunks",
    "qdrant_client",
    "re",
    "record_export",
    "resume_state",
    "run_chunk",
    "sha256_file",
    "stream_parquet",
    "sys",
    "tempfile",
    "time",
    "upload_file",
]
