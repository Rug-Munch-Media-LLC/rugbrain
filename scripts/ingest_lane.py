"""Single ingest lane resolution — probe-gated, file-first key, fail loud.

One job: decide the ONE corpus-valid embedding lane for an ingest BEFORE any
write, and abort with a typed error that names the lane. No silent local
fallback: the local :8024 lane answered 401 four times while ingests kept
"working" (upserted=0), so the lane must be proven by the probe first.
"""

from __future__ import annotations

import os

from embed_probe import probe

LANE = "siliconflow-sl"
URL = "https://api.siliconflow.com/v1/embeddings"
MODEL = "Qwen/Qwen3-Embedding-8B"
KEY_FILE = "/srv/secrets/siliconflow_api_key"
KEY_ENV = "SILICONFLOW_API_KEY"
CORPUS_DIM = 4096


class EmbedLaneUnavailable(RuntimeError):
    """No corpus-valid (4096-d) lane could be proven for this ingest."""


def resolve_key() -> str:
    """Key file first (stale env never wins), then SILICONFLOW_API_KEY."""
    try:
        raw = open(KEY_FILE, encoding="utf-8").read().strip()
    except OSError:
        raw = ""
    if raw:
        return raw.splitlines()[0].strip()
    value = os.getenv(KEY_ENV, "").strip()
    if value:
        return value
    raise EmbedLaneUnavailable(
        f"ingest aborted: embed lane {LANE} unusable (reason=missing_key "
        f"key_file={KEY_FILE} env={KEY_ENV})"
    )


def resolve_ingest_lane() -> dict:
    """Probe the default lane (env EMBED_PROVIDER, default siliconflow) and return it."""
    wanted = os.getenv("EMBED_PROVIDER", "").strip().lower() or "siliconflow"
    if wanted not in ("siliconflow", "siliconflow-sl"):
        raise EmbedLaneUnavailable(
            f"ingest aborted: embed lane {wanted} unusable (reason=not_corpus_valid "
            f"allowed=siliconflow)"
        )
    key = resolve_key()
    result = probe(URL, key, MODEL, timeout=60)
    if not result.get("ok") or result.get("dim") != CORPUS_DIM:
        raise EmbedLaneUnavailable(
            f"ingest aborted: embed lane {LANE} unusable (reason="
            f"{result.get('error') or 'dim_' + str(result.get('dim'))} "
            f"want_dim={CORPUS_DIM} url={URL})"
        )
    return {"lane": LANE, "url": URL, "model": MODEL, "key": key, "dim": CORPUS_DIM,
            "ms": result.get("ms")}
