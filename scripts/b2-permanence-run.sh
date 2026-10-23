#!/bin/bash
# b2-permanence-run.sh — systemd entrypoint for the CHUNKED permanence run.
#
# Why chunks: the whole day did not fit one 6h unit. Measured 2026-10-23: 77 of
# 85 collections in 5.99h, then `TimeoutStartSec` fired and SIGTERM'd the run at
# [78/85] — the fourth consecutive night. The unit timeout is NOT raised; the
# work is sized to fit it instead. Each invocation runs ONE chunk (default
# budget 80 min against a 90 min unit timeout), resumes from today's manifest,
# and the timer fires the next chunk a few times through the morning.
#
# Retention policy (BACKUP-POLICY.md, RAG permanence): keep 30 daily / 4 weekly
# / 12 monthly dated snapshots. LIVE deletion is gated behind B2_PRUNE_LIVE=1
# (operator opt-in, from the unit's EnvironmentFile). Default = dry-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARGS=(
	--prune
	--auto-chunk
	--chunk-count "${B2_CHUNK_COUNT:-4}"
	--max-seconds "${B2_CHUNK_SECONDS:-4800}"
	--shard-points "${B2_SHARD_POINTS:-400000}"
)
if [ "${B2_PRUNE_LIVE:-0}" = "1" ]; then
	ARGS+=(--prune-live)
fi

exec /usr/bin/python3 "${SCRIPT_DIR}/b2_permanence.py" "${ARGS[@]}"
