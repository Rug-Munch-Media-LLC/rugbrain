#!/usr/bin/env bash
# sync_code_knowledge.sh — daily sync of RugBrain coding knowledge with the
# RMI Codebase MCP updates.
#
# What it does (idempotent — safe to run every day):
#   1. Refresh the RMI Codebase MCP index (rmi_refresh) so codebase search
#      reflects recent repo changes.
#   2. Pull fresh security patterns from the MCP ChromaDB into the RAG
#      `coding_knowledge` collection (via rugbrain.mcp_bridge sync).
#   3. Re-run the three coding-knowledge ingesters (Solidity / Rust-Solana /
#      Move-Cairo) — deterministic point IDs make re-runs overwrite in place.
#   4. Verify end-to-end with verify_coding_knowledge.py.
#
# Usage:
#   scripts/sync_code_knowledge.sh [--full]
#     --full  also fetch live docs + clone reference repos (default: curated
#             + MCP patterns only, which is fast and offline-friendly)
#
# Cron (daily 03:15 UTC):
#   15 3 * * *  /path/to/rugbrain/scripts/sync_code_knowledge.sh >> /var/log/rugbrain/sync_code_knowledge.log 2>&1
#
# Requires (host):
#   - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
#   - at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-8b
#   - RMI Codebase MCP server dir at RMI_MCP_DIR (default /path/to/rmi-codebase-mcp)
#   - RMI_MCP_PYTHON with fastmcp+chromadb (default x402 venv)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PYTHON="${PYTHON:-$REPO_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

# ── Environment (respect existing env, else local defaults) ─────────────
export QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}"
export INFINITY_URL="${INFINITY_URL:-http://172.22.0.1:8004/v1}"
export RAG_CODEBASE_DIR="${RAG_CODEBASE_DIR:-/tmp/rag_codebases}"
export RMI_MCP_DIR="${RMI_MCP_DIR:-/path/to/rmi-codebase-mcp}"
export RMI_MCP_PYTHON="${RMI_MCP_PYTHON:-/path/to/rmi-mcp-x402/.venv/bin/python}"
export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
export REDIS_PORT="${REDIS_PORT:-6379}"

MODE="${1:-}"
if [ "$MODE" = "--full" ]; then
  echo "[sync] FULL mode — fetch live docs + clone reference repos"
fi

log() { echo "[sync $(date -u +%FT%TZ)] $*"; }

# Wait for to be ready (it can restart / warm up on CPU).
wait_() {
  for _ in $(seq 1 40); do
    if curl -fsS -m 10 "$INFINITY_URL/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 15
  done
  log "WARN: not reachable after wait — continuing (embedding may fail)"
  return 0
}

step() { log "── $1"; }

# ── 1. Refresh the Codebase MCP index ────────────────────────────────────
step "refresh RMI Codebase MCP index (rmi_refresh)"
if [ -d "$RMI_MCP_DIR" ] && [ -x "$(dirname "$RMI_MCP_PYTHON")/python" ]; then
  "$PYTHON" -m rugbrain.mcp_bridge status >/dev/null 2>&1 &&
    "$PYTHON" - <<'PY'
import asyncio, json, sys
from rugbrain.mcp_bridge import _call_mcp

async def main() -> int:
    try:
        res = await _call_mcp("refresh", {"project": None})
        print(f"  refresh status: {res.get('status', 'unknown')} — {len(res.get('results', []))} repos")
        return 0
    except Exception as exc:
        print(f"  refresh skipped: {exc}")
        return 0

sys.exit(asyncio.run(main()))
PY
else
  log "MCP dir not found — skipping index refresh"
fi

# ── 2. Pull MCP security patterns into RAG coding_knowledge ─────────────
step "pull MCP security patterns into coding_knowledge"
wait_
"$PYTHON" -m rugbrain.mcp_bridge sync || log "WARN: mcp pattern sync returned non-zero"

# ── 3. Re-run coding-knowledge ingesters (idempotent) ────────────────────
step "re-run coding-knowledge ingesters"
wait_

# Unified ingest CLI — coding subcommand delegates to individual lang ingesters
if [ "$MODE" = "--full" ]; then
  "$PYTHON" scripts/ingest.py coding --lang all || log "WARN: coding ingest failed"
else
  # Fast path: curated docs only (no network fetch/clone), idempotent.
  "$PYTHON" scripts/ingest.py coding --lang all --no-network || log "WARN: coding ingest failed"
fi

# ── 4. Verify ────────────────────────────────────────────────────────────
step "verify coding knowledge"
set +e
"$PYTHON" scripts/verify_coding_knowledge.py
VERIFY_RC=$?
set -e
if [ "$VERIFY_RC" -eq 0 ]; then
  log "RESULT: ALL PASS"
else
  log "RESULT: SOME FAILED (rc=$VERIFY_RC)"
fi

log "done"
exit "$VERIFY_RC"
