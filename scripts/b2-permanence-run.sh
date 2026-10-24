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

BASE=(
	--auto-chunk
	--chunk-count "${B2_CHUNK_COUNT:-4}"
	--max-seconds "${B2_CHUNK_SECONDS:-1800}"
	--slot-seconds "${B2_SLOT_SECONDS:-2100}"
	--carry-days "${B2_CARRY_DAYS:-0}"
	--shard-points "${B2_SHARD_POINTS:-50000}"
)
if [ "${B2_PRUNE_LIVE:-0}" = "1" ]; then
	BASE+=(--prune-live)
fi

# FILL THE SLOT (2026-10-24, measured). One chunk per firing wasted the slot:
# at 04:34:03 --auto-chunk picked a chunk owing 0.6 min, the run finished in
# 1.2 min, and 44 of the 45 minutes available were never used. The plan for
# 85 collections is 137.3 min of real work against 19 x 30 min of slots, so
# the budget was never the constraint -- UTILISATION was. Run chunk after
# chunk until the slot deadline; a finished day exits in seconds per chunk.
DEADLINE=$(( $(date +%s) + ${B2_SLOT_SECONDS:-2100} ))
MAX_CHUNKS=${B2_MAX_CHUNKS_PER_SLOT:-4}
first=1
for _ in $(seq 1 "${MAX_CHUNKS}"); do
	remaining=$(( DEADLINE - $(date +%s) ))
	if [ "${remaining}" -lt 90 ]; then
		break
	fi
	ARGS=("${BASE[@]}" --max-seconds "$(( remaining - 60 ))" --slot-seconds "${remaining}")
	if [ "${first}" = "1" ]; then
		# --prune once per slot, not once per chunk: it is a dry-run scan of
		# every dated dir and costs ~1 min.
		ARGS=(--prune "${ARGS[@]}")
		first=0
	fi
	/usr/bin/python3 "${SCRIPT_DIR}/b2_permanence.py" "${ARGS[@]}" || exit $?
done
exit 0
