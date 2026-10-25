#!/usr/bin/env bash
# Refresh the bot / launchpad / whitepaper documentation corpus.
#
# Fetches the public pages listed in config/bots_launchpads_manifest.json,
# re-extracts them (Docling for PDFs, markitdown for HTML), and upserts the
# changed chunks into research_v2 (routed to research_v2_8b, 4096-d).
# Unchanged chunks are skipped by the store's deterministic point ids, so a
# no-change run costs embedding only for what actually moved.
#
# Usage:
#   scripts/refresh_bots_launchpads.sh              # full refresh
#   scripts/refresh_bots_launchpads.sh --limit 25   # bounded batch
#   DRY_RUN=1 scripts/refresh_bots_launchpads.sh    # fetch + report only
#
# Exit codes: 0 ok · 1 no documents fetched · 2 embedder/Qdrant unavailable.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
REPORT_DIR="${REPORT_DIR:-${REPO_ROOT}/data}"
REPORT="${REPORT_DIR}/bots_launchpads_report.json"
LOG="${REPORT_DIR}/bots_launchpads_refresh.log"

mkdir -p "${REPORT_DIR}"

ARGS=(--report "${REPORT}")
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  ARGS+=(--dry-run)
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit|--category)
      ARGS+=("$1" "${2:?$1 needs a value}")
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

echo "[$(date -Is)] refresh start args=${ARGS[*]}" | tee -a "${LOG}"
set +e
"${PYTHON_BIN}" scripts/ingest_bots_launchpads.py "${ARGS[@]}" 2>&1 | tee -a "${LOG}"
status=${PIPESTATUS[0]}
set -e
echo "[$(date -Is)] refresh done status=${status} report=${REPORT}" | tee -a "${LOG}"
exit "${status}"
