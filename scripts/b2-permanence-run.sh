#!/bin/bash
# b2-permanence-run.sh — systemd entrypoint for the CHUNKED permanence run.
#
# Why chunks: the whole day did not fit one 6h unit. Measured 2026-10-23: 77 of
# 85 collections in 5.99h, then `TimeoutStartSec` fired and SIGTERM'd the run at
# [78/85] — the fourth consecutive night. The unit timeout is NOT raised; the
# work is sized to fit it instead. Each invocation runs ONE chunk, resumes from
# today's manifest, and the timer fires the next chunk every 40 min.
#
# TWO budgets (2026-10-23, measured): --max-seconds (soft, 1800 s) is when an
# in-flight collection stops scrolling and lands what it has; --slot-seconds
# (hard, 2100 s) is when a NEW unit may no longer be started. The hard limit
# sits 5 min inside the 40 min timer slot, because the 15:00 run spent 43.3 min
# against a 30 min budget, lost the 15:40 slot outright, and deferred
# wallet_profiles_8b with zero shards. Raising the unit timeout was not the fix
# then and is not now — the SLOT is the clock.
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
	--max-seconds "${B2_CHUNK_SECONDS:-1800}"
	--slot-seconds "${B2_SLOT_SECONDS:-2100}"
	--carry-days "${B2_CARRY_DAYS:-0}"
	--shard-points "${B2_SHARD_POINTS:-50000}"
)
if [ "${B2_PRUNE_LIVE:-0}" = "1" ]; then
	ARGS+=(--prune-live)
fi

exec /usr/bin/python3 "${SCRIPT_DIR}/b2_permanence.py" "${ARGS[@]}"
