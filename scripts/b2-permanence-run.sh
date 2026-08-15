#!/bin/bash
# b2-permanence-run.sh — systemd wrapper for b2_permanence.py that wires the
# retention prune into the nightly rmi-rag-permanence.timer (03:00).
#
# Retention policy (BACKUP-POLICY.md, RAG permanence): keep 30 daily /
# 4 weekly / 12 monthly dated snapshots. The prune pass runs every night;
# LIVE deletion is gated behind B2_PRUNE_LIVE=1 (operator opt-in, read from
# the unit's EnvironmentFile). Default (unset/0) = dry-run prune only —
# it lists what WOULD be deleted and deletes nothing.
#
# Opt in to live pruning: add `B2_PRUNE_LIVE=1` to b2-permanence.env, then:
#   systemctl restart rmi-rag-permanence.service
#
# Self-locating so this script can be deployed from the public repo without
# hardcoding internal paths (see systemd/rmi-rag-permanence.service).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARGS=(--prune)
if [ "${B2_PRUNE_LIVE:-0}" = "1" ]; then
  ARGS+=(--prune-live)
fi

exec /usr/bin/python3 "${SCRIPT_DIR}/b2_permanence.py" "${ARGS[@]}"
