#!/usr/bin/env bash
# rmi-news-ingest — RSS ingest + news → embed → Qdrant (15-min cron).
#
# 1. Runs rmi-backend RSS ingest (populates Postgres news_items).
# 2. Runs scripts/news_to_rag.py (chunks + embeds + upserts Qdrant `news`).
#
# Scheduled via crontab (see below) or the rmi-news-ingest systemd timer
# in systemd/ (deploy: cp systemd/rmi-news-ingest.* /etc/systemd/system/).
#
#   */15 * * * * /bin/bash /path/to/rugbrain/scripts/rmi-news-ingest.sh >> /var/log/rmi-news-ingest.log 2>&1
#
# Secrets: reads POSTGRES_PASSWORD / REDIS_PASSWORD from /path/to/backend.env.
set -euo pipefail

# ── Concurrency guard: never run two ingests at once ─────────────────
# Cron fires every 15 min but a run can exceed 15 min (Infinity cold
# starts, slow RSS). Without a lock, runs pile up and saturate Infinity.
# flock -n fails fast: if another run holds the lock we exit 0 (skip,
# the next scheduled run will pick up). The lock is auto-released when
# this process exits (fd 200 closed), plus an explicit EXIT trap.
LOCK_FILE="${RMI_NEWS_INGEST_LOCK:-/tmp/rmi-news-ingest.lock}"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date -u +%FT%TZ)] another ingest already running (lock: $LOCK_FILE), skipping" >&2
  exit 0
fi
trap 'flock -u 200 2>/dev/null || true' EXIT

# ── Log rotation (backstop; primary is /etc/logrotate.d/rmi-news-ingest) ──
# Cron appends to this log every 15 min; a failing pipeline (e.g. the
# missing news_items table) can blow it up. copytruncate semantics keep
# the fd 1/2 that cron already opened on the live inode: copy the file,
# then truncate in place so this run keeps appending. Keep 7, gzip'd.
# Override: RMI_NEWS_INGEST_LOG, RMI_NEWS_INGEST_LOG_MAX (bytes).
LOG_FILE="${RMI_NEWS_INGEST_LOG:-/var/log/rmi-news-ingest.log}"
LOG_MAX="${RMI_NEWS_INGEST_LOG_MAX:-52428800}" # 50 MB
if [ -f "$LOG_FILE" ]; then
  size=$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$size" -gt "$LOG_MAX" ]; then
    i=6
    while [ "$i" -ge 1 ]; do
      if [ "$i" -eq 6 ]; then
        rm -f "${LOG_FILE}.${i}.gz" 2>/dev/null || true
      else
        mv -f "${LOG_FILE}.${i}.gz" "${LOG_FILE}.$((i + 1)).gz" 2>/dev/null || true
      fi
      i=$((i - 1))
    done
    cp -p "$LOG_FILE" "${LOG_FILE}.1" 2>/dev/null || true
    gzip -f "${LOG_FILE}.1" 2>/dev/null || true
    : >"$LOG_FILE" 2>/dev/null || true
    log "log rotated (size=$size > ${LOG_MAX} bytes); 7 compressed kept"
  fi
fi

BACKEND_DIR="${RMI_BACKEND_DIR:-/path/to/rmi-backend}"
RAG_DIR="${RMI_RAG_DIR:-/path/to/rugbrain}"
PYTHON="${RMI_BACKEND_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
# Step 2 (news_to_rag) imports rugbrain.* from the RAG repo's own venv;
# the backend venv does not ship rugbrain. Using RAG_PYTHON keeps the
# embed→Qdrant half of the pipeline alive.
RAG_PYTHON="${RMI_RAG_PYTHON:-$RAG_DIR/.venv/bin/python}"
SECRETS="${RMI_SECRETS_FILE:-/path/to/backend.env}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# ── Load secrets (never echoed) ──────────────────────────────────────
if [ -f "$SECRETS" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS"
  set +a
fi

# ── Step 1: RSS ingest into Postgres ────────────────────────────────
log "step=ingest dir=$BACKEND_DIR"
if [ -x "$PYTHON" ]; then
  (cd "$BACKEND_DIR" && POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" "$PYTHON" -m app.domains.news.ingest)
else
  (cd "$BACKEND_DIR" && POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" python3 -m app.domains.news.ingest)
fi

# ── Step 2: news → embed → Qdrant ───────────────────────────────────
log "step=news_to_rag dir=$RAG_DIR"
if [ -x "$RAG_PYTHON" ]; then
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}" \
      INFINITY_URL="${INFINITY_URL:-http://127.0.0.1:8001}" \
      "$RAG_PYTHON" scripts/news_to_rag.py)
else
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}" \
      INFINITY_URL="${INFINITY_URL:-http://127.0.0.1:8001}" \
      python3 scripts/news_to_rag.py)
fi

log "step=done"
