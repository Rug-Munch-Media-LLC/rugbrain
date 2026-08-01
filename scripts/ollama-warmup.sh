#!/usr/bin/env bash
#
# rmi-ollama entrypoint: start ollama serve, wait for readiness, then
# pre-load the RAG embedding models so the first search doesn't pay a
# 30-45s cold-start penalty.
#
# OLLAMA_KEEP_ALIVE=-1 (set in docker-compose) keeps the loaded models
# resident forever, so the warmup here is only needed after container
# (re)starts.
#
# Usage: entrypoint of rmi-ollama container (mounted read-only).
set -euo pipefail

log() { printf '[ollama-warmup] %s\n' "$*"; }

# Start the server in the background so we can warm models while it runs.
ollama serve &
SERVE_PID=$!

cleanup() {
  log "received signal, stopping ollama serve (pid ${SERVE_PID})"
  kill "${SERVE_PID}" 2>/dev/null || true
  wait "${SERVE_PID}" 2>/dev/null || true
}
trap cleanup TERM INT

# Wait for the API to accept requests (ollama list hits the local server).
READY=0
for _ in $(seq 1 90); do
  if ollama list >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "${SERVE_PID}" 2>/dev/null; then
    log "ollama serve exited unexpectedly"
    exit 1
  fi
  sleep 1
done

if [ "${READY}" -ne 1 ]; then
  log "timed out waiting for ollama API; continuing anyway"
fi

# Pre-load the embedding models used by RAG search (bge-m3 primary,
# nomic-embed-text fallback). Warmup via the CLI keeps them resident
# because KEEP_ALIVE=-1.
WARMUP_MODELS=(bge-m3 nomic-embed-text)
for model in "${WARMUP_MODELS[@]}"; do
  log "pre-loading ${model}"
  ollama run "${model}" "warmup" >/dev/null 2>&1 || log "warmup of ${model} failed (non-fatal)"
done

log "models pre-loaded; serving requests"
# Keep serving. On SIGTERM/SIGINT the trap forwards to the server.
wait "${SERVE_PID}"
