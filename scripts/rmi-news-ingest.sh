#!/usr/bin/env bash
# rmi-news-ingest — RSS ingest + news → embed → Qdrant (15-min cron).
#
# 1. Runs RugBrain-native RSS ingest (ingest_cryptopanic_news.py → Postgres news_items).
# 2. Runs scripts/news_to_rag.py (chunks + embeds + upserts Qdrant `news`).
#
# Scheduled via crontab (see below) or the rmi-news-ingest systemd timer
# in systemd/ (deploy: cp systemd/rmi-news-ingest.* /etc/systemd/system/).
#
#   */15 * * * * /bin/bash /path/to/rugbrain/scripts/rmi-news-ingest.sh >> /var/log/rmi-news-ingest.log 2>&1
#
# Secrets: reads POSTGRES_PASSWORD / REDIS_PASSWORD from RMI_SECRETS_FILE (if set).
set -euo pipefail

# ── Concurrency guard: never run two ingests at once ─────────────────
# Cron fires every 15 min but a run can exceed 15 min (cold
# starts, slow RSS). Without a lock, runs pile up and saturate .
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

RAG_DIR="${RMI_RAG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# news_to_rag imports rugbrain.* from the RAG repo's own venv.
RAG_PYTHON="${RMI_RAG_PYTHON:-$RAG_DIR/.venv/bin/python}"
SECRETS="${RMI_SECRETS_FILE:-}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# ── Load secrets (never echoed) ──────────────────────────────────────
if [ -f "$SECRETS" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS"
  set +a
fi

# ── Step 1: RugBrain-native RSS ingest into Postgres ────────────────
# (replaces the retired rmi-backend RSS ingest — fully independent)
# Multi-feed mode (2026-08-29): data/rss_feeds.txt carries the 177-feed
# list (security firms, L1/L2 blogs, regulators, mainstream crypto press).
# Falls back to the single CryptoPanic feed when the file is absent.
FEEDS_FILE="${RMI_FEEDS_FILE:-$RAG_DIR/data/rss_feeds.txt}"
FEED_ARGS=(--days "${RMI_NEWS_DAYS:-7}")
if [ -f "$FEEDS_FILE" ]; then
  FEED_ARGS+=(--feeds-file "$FEEDS_FILE")
fi
log "step=ingest_cryptopanic dir=$RAG_DIR feeds_file=$FEEDS_FILE"
if [ -x "$RAG_PYTHON" ]; then
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      "$RAG_PYTHON" scripts/ingest_cryptopanic_news.py "${FEED_ARGS[@]}")
else
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      python3 scripts/ingest_cryptopanic_news.py "${FEED_ARGS[@]}")
fi

# ── Step 2: news → embed → Qdrant ───────────────────────────────────
log "step=news_to_rag dir=$RAG_DIR"
if [ -x "$RAG_PYTHON" ]; then
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}" \
      EMBEDDER_URL="${INFINITY_URL:-http://127.0.0.1:8004/v1}" \
      "$RAG_PYTHON" scripts/news_to_rag.py)
else
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}" \
      EMBEDDER_URL="${INFINITY_URL:-http://127.0.0.1:8004/v1}" \
      python3 scripts/news_to_rag.py)
fi

log "step=done"
