#!/usr/bin/env bash
# rmi-news-ingest — RSS ingest + news → embed → Qdrant (15-min cron).
#
# 1. Runs rmi-backend RSS ingest (populates Postgres news_items).
# 2. Runs scripts/news_to_rag.py (chunks + embeds + upserts Qdrant `news`).
#
# Scheduled via crontab (see below) or the rmi-news-ingest systemd timer
# in systemd/ (deploy: cp systemd/rmi-news-ingest.* /etc/systemd/system/).
#
#   */15 * * * * /bin/bash /srv/work/repos/rmi-rag/scripts/rmi-news-ingest.sh >> /var/log/rmi-news-ingest.log 2>&1
#
# Secrets: reads POSTGRES_PASSWORD / REDIS_PASSWORD from /srv/secrets/backend.env.
set -euo pipefail

BACKEND_DIR="${RMI_BACKEND_DIR:-/srv/work/repos/rmi-backend}"
RAG_DIR="${RMI_RAG_DIR:-/srv/work/repos/rmi-rag}"
PYTHON="${RMI_BACKEND_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
SECRETS="${RMI_SECRETS_FILE:-/srv/secrets/backend.env}"

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
if [ -x "$PYTHON" ]; then
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}" \
      OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}" \
      "$PYTHON" scripts/news_to_rag.py)
else
  (cd "$RAG_DIR" &&
    POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}" \
      QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}" \
      OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}" \
      python3 scripts/news_to_rag.py)
fi

log "step=done"
