# Cutover: X social-intel ingestion → rugbrain

Date: 2026-09-18. The X/Twitter social-intel ingestion pipeline moved OUT of
`rugmunchbot` INTO `rugbrain` (RAG infra owns it; the bot is now a consumer of
the indexed corpus, not the ingester).

## What moved

| Old (rugmunchbot) | New (rugbrain) |
|---|---|
| `tools/getxapi.py` | `src/rugbrain/ingest_sources/x_intel/client.py` + `normalize.py` |
| `tools/getxapi_config.py` | `src/rugbrain/ingest_sources/x_intel/config.py` |
| `tools/getxapi_budget.py` (accounting) | `src/rugbrain/ingest_sources/x_intel/budget.py` |
| `tools/getxapi_budget.py` (routing) | `src/rugbrain/ingest_sources/x_intel/routing.py` |
| `tools/x_ingest.py` (Pry helpers) | `src/rugbrain/ingest_sources/x_intel/pry_lane.py` |
| `tools/x_ingest.py` (pipeline) | `src/rugbrain/ingest_sources/x_intel/pipeline.py` |
| `docs/getxapi-setup.md` | `docs/getxapi-setup.md` (this repo) |

Fixes applied during the port:
- **Base URL**: `https://api.getxapi.com` (no `/v1` — the legacy suffix 404s; live-verified 2026-09-18).
- **Budget**: ceiling $4.65 (live `balance_total`), hard daily cap 150 calls, plan credits expire **2026-10-02**.
- All files ≤200 lines, zero monkeypatching, async, fail-open.

## How to enable

On the production host:

```bash
cd /srv/work/repos/rugbrain
sudo cp deploy/systemd/rmi-rag-x-ingest.service /etc/systemd/system/
sudo cp deploy/systemd/rmi-rag-x-ingest.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rmi-rag-x-ingest.timer
```

Manual one-off cycle:

```bash
cd /srv/work/repos/rugbrain && .venv/bin/python -m rugbrain.ingest_sources.x_intel --cycle
```

Environment: `/srv/secrets/rugbrain-tg.env` (Qdrant/Redis/embed) is required;
optionally `/srv/secrets/rugbrain-x.env` for `GETXAPI_KEY` / `PRY_API_KEY`
overrides (the GetXAPI key file at `/srv/secrets/getxapi_api_key` is read
directly when running as root).

## Rollback

```bash
sudo systemctl disable --now rmi-rag-x-ingest.timer
```

Then re-enable the rugmunchbot X-ingest lane (pre-cutover code is in
rugmunchbot git history).

## Follow-ups

- [ ] Remove the rugmunchbot X-ingest scheduler wiring once the timer has run
      cleanly for a week.
- [ ] Monitor the Oct-2 plan-credit expiry — top up or the lane fails open to
      Pry-only.
- [ ] Consider a Qdrant payload index on `metadata.handle` for per-account
      filtering.
