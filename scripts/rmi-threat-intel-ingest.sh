#!/usr/bin/env bash
# rmi-threat-intel-ingest — threat-intel + regulation enrichment → Qdrant
# (every-6h cron; complements the 15-min news pipeline and the hourly
# rag-knowledge-refresh.sh).
#
# 1. scripts/ingest_defillama_hacks.py --days 90
#      Fresh DeFi exploit reports (DeFiLlama hacks API) → threat_intel_v2
#      (the real hybrid collection; the legacy "threat_intel" name was
#      migrated away — no alias exists). Replaces the dead Rekt News RSS feed.
# 2. scripts/ingest_regulation_enforcement.py --days 90
#      SEC litigation/press release RSS (crypto-filtered) + landmark seed
#      → regulation_enforcement (new hybrid collection).
#
# Scheduled via crontab (see below) or a systemd timer:
#
#  30 */6 * * * /bin/bash /path/to/rmi-rag/scripts/rmi-threat-intel-ingest.sh >> /var/log/rmi-threat-intel-ingest.log 2>&1
#
# No secrets: reads QDRANT_URL / INFINITY_URL from env or defaults.
set -euo pipefail

# ── Concurrency guard (never run two ingests at once) ─────────────────
LOCK_FILE="${RMI_THREAT_INTEL_LOCK:-/tmp/rmi-threat-intel-ingest.lock}"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date -u +%FT%TZ)] another ingest already running (lock: $LOCK_FILE), skipping" >&2
  exit 0
fi
trap 'flock -u 200 2>/dev/null || true' EXIT

RAG_DIR="${RMI_RAG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${RMI_RAG_PYTHON:-$RAG_DIR/.venv/bin/python}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi

# Bulk lane: DeepInfra Qwen3-Embedding-4B (same 2560-dim space as the local
# server; ~80x faster). Keeps heavy ingestion off the CPU-capped local
# llama.cpp process — see rugbrain/scripts/embed_lane.py.
export EMBED_PROVIDER="${EMBED_PROVIDER:-deepinfra}"
export DEEPINFRA_KEY_FILE="${DEEPINFRA_KEY_FILE:-/srv/secrets/deepinfra_api_key}"

log "step=defillama_hacks"
(cd "$RAG_DIR" &&
  QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}" \
    INFINITY_URL="${INFINITY_URL:-http://127.0.0.1:8004/v1}" \
    timeout 3600 "$PYTHON" scripts/ingest_defillama_hacks.py --days 90)

log "step=regulation_enforcement"
(cd "$RAG_DIR" &&
  QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}" \
    INFINITY_URL="${INFINITY_URL:-http://127.0.0.1:8004/v1}" \
    timeout 3600 "$PYTHON" scripts/ingest_regulation_enforcement.py --days 90)

# ── step=ingest_all_sources (2026-08-29) ─────────────────────────────
# Full 48-source registry (CertiK alerts, SlowMist archive, CISA KEV,
# OpenPhish, URLHaus, OpenSanctions, blacklists, OSSF malicious packages,
# DeFiHackLabs, Rekt, DeFiLlama, MEV, bridges, EIPs, …) → Qdrant.
# Idempotent: deterministic doc_ids + has_point skip, so each 6h run only
# embeds NEW docs. DeepInfra qwen3-4b first, local llama.cpp fallback.
log "step=ingest_all_sources"
(cd "$RAG_DIR" &&
  QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}" \
    QWEN_EMBED_URL="${INFINITY_URL:-http://127.0.0.1:8004/v1}" \
    timeout 3600 "$PYTHON" scripts/ingest_all_sources.py)

log "step=done"
