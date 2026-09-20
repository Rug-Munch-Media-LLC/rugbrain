#!/bin/bash
# consolidate-active.sh — incremental sync of the actively-ingested collections
# into rag_documents (unified reads serve it). Cheap: only new points are
# embedded (existence check per batch). See systemd/rugbrain-consolidate.timer.
set -uo pipefail
cd /srv/work/repos/rugbrain
for src in news_v2 social_intel known_scams scam_intel_v2 threat_intel_v2 wallet_v2 stablecoin_market market_health live_threats security_feeds; do
  .venv/bin/python scripts/consolidate_to_rag_documents.py --source "$src" --apply 2>&1 | grep -E "done:|points\)" | tail -2
done
