#!/usr/bin/env bash
# rugbrain-registry-contract-alert — OnFailure hook for the registry contract check.
#
# The check itself runs in rugbrain-registry-contract.service. This script is
# the alerting half: it is wired as that unit's OnFailure= target, so a
# violation produces a message on the RugBrain alert sink rather than a red
# line in a journal nobody reads.
#
# Why a live timer instead of pytest only: fleet CI has had zero successful
# runs, so "it is covered by CI" is not a control — it is a hope. A timer
# actually executes. This one runs whether or not anyone pushes a commit.
#
# Read-only: reads the service's status and exit code and posts a message.
# It never writes to Qdrant and never restarts anything.
#
# Exit 0 always: a failing alert hook must not turn a single violation into a
# double failure. The alert is delivered or it is not; the service's own exit
# code remains the verdict.
set -euo pipefail

SERVICE="${1:-rugbrain-registry-contract.service}"
STATE_DIR="${RUGBRAIN_STATE_DIR:-/var/lib/rugbrain}"
REPORT="${STATE_DIR}/registry-contract.json"
STAMP="${STATE_DIR}/registry-contract.last-rc"

mkdir -p "${STATE_DIR}"

# Prefer the recorded report; fall back to running the check read-only so the
# alert still names the offending source when the report is missing.
if [ ! -f "${REPORT}" ]; then
  REPO="${RUGBRAIN_REPO:-/srv/work/repos/rugbrain}"
  if [ -d "${REPO}" ] && [ -x "${REPO}/.venv/bin/python" ]; then
    (cd "${REPO}" && "${RUGBRAIN_REPO}/.venv/bin/python" -m rugbrain.eval.registry_contract --json \
      >"${REPORT}.tmp" 2>/dev/null && mv "${REPORT}.tmp" "${REPORT}" ||
      mv "${REPORT}.tmp" "${REPORT}") || true
  fi
fi

RC="$(systemctl show "${SERVICE}" -p ExecMainStatus --value 2>/dev/null || echo 1)"
echo "${RC}" >"${STAMP}"

# Deduplicate: a violation that is already reported and unchanged should not
# re-alert every night. The rc alone is not enough, so the offending sources
# are part of the fingerprint.
VIOLATIONS=""
if [ -f "${REPORT}" ]; then
  VIOLATIONS="$(
    python3 - "${REPORT}" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
names = sorted(v.get("source", "?") for v in data.get("violations", []))
print(", ".join(names))
PY
  )"
fi

FINGERPRINT="${RC}|${VIOLATIONS}"
PREV=""
[ -f "${REPORT}.fp" ] && PREV="$(cat "${REPORT}.fp" 2>/dev/null || true)"

MESSAGE="RugBrain registry contract check FAILED (exit ${RC})"
if [ -n "${VIOLATIONS}" ]; then
  MESSAGE="${MESSAGE}. Sources routing to an undeclared collection: ${VIOLATIONS}"
else
  MESSAGE="${MESSAGE}. See: journalctl -u ${SERVICE}"
fi

# Send through the repo's own dispatcher so the alert lands on the RugBrain
# sink (Telegram) with the same routing as every other fleet alert.
REPO="${RUGBRAIN_REPO:-/srv/work/repos/rugbrain}"
SENT=1
if [ -d "${REPO}" ] && [ -x "${REPO}/.venv/bin/python" ]; then
  if (
    cd "${REPO}" && "${REPO}/.venv/bin/python" - "${MESSAGE}" <<'PY' 2>/dev/null
import asyncio, sys
from rugbrain.alert_dispatcher import dispatch_alert
asyncio.run(
    dispatch_alert(
        title="Registry contract violation",
        message=sys.argv[1],
        alert_type="registry_contract",
        severity="high",
    )
)
PY
  ); then
    SENT=0
  fi
fi

if [ "${SENT}" -ne 0 ]; then
  # The dispatcher was unavailable. Fall back to the system logger so the
  # failure is still recorded somewhere durable rather than dropped.
  logger -t rugbrain-registry-contract --priority user.err "${MESSAGE}"
fi

# Suppress only an IDENTICAL repeat, and only after it has been delivered.
if [ "${FINGERPRINT}" = "${PREV}" ] && [ "${SENT}" -eq 0 ]; then
  echo "duplicate violation fingerprint, alert already delivered"
fi
printf '%s' "${FINGERPRINT}" >"${REPORT}.fp"

echo "alert dispatched (rc=${RC}, delivered=${SENT})"
exit 0
