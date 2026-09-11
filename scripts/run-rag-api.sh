#!/usr/bin/env bash
# Start the RugBrain RAG API (systemd: rag-api.service).
#   uvicorn rugbrain.main:app on 127.0.0.1:8012
set -euo pipefail
cd /srv/work/repos/rugbrain
export RAG_API_HOST="${RAG_API_HOST:-127.0.0.1}"
export RAG_API_PORT="${RAG_API_PORT:-8012}"
exec .venv/bin/python -m rugbrain --host 0.0.0.0 --port "$RAG_API_PORT"
