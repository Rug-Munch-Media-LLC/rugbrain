#!/usr/bin/env bash
# scrape-dead-sources — recover articles from dead-RSS sources into news_items.
#
# Runs the sitemap-first recovery (FlareSolverr/Pry fallbacks) for sources
# flagged DEAD in the databus status file whose sites still publish. Rows land
# in Postgres news_items; the 15-min rmi-news-ingest cron embeds them into
# news_v2_8b on its next run.
#
# Secrets: /srv/rmi-infra/.env.secrets (POSTGRES_*) and
# /srv/secrets/rugbrain-x.env (PRY_* — the internal HMAC caller is the pry
# bridge gateway, NOT loopback: PRY_INTERNAL_CALLER=172.28.0.1).
set -euo pipefail

LOCK_FILE="${SCRAPE_DEAD_LOCK:-/tmp/scrape-dead-sources.lock}"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date -u +%FT%TZ)] another run holds the lock, skipping" >&2
  exit 0
fi

set -a
# shellcheck disable=SC1091
source /srv/rmi-infra/.env.secrets
# shellcheck disable=SC1091
source /srv/secrets/rugbrain-x.env
set +a

cd /srv/work/repos/rugbrain
exec .venv/bin/python scripts/scrape_dead_sources.py --max-per-source 10 "$@"
