# RugBrain Standalone Migration Plan — retire rmi-rag, sever the backend cord

> 2026-08-25 · Owner: build seat · Status: **IN PROGRESS**
> Goal: `/srv/work/repos/rugbrain` is the ONLY checkout; the intel RAG runs,
> ingests, and serves without rmi-backend being up.

## Where we are

| Item | State |
|---|---|
| Canonical checkout | `/srv/work/repos/rugbrain` (clean, synced, `.venv` ready) |
| `.env.rag` | copied to rugbrain ✓ (gitignored) |
| `model_gateway` | already runs from rugbrain ✓ |
| `migrate-embeds.service` | still cwd=rmi-rag — dies naturally when migration completes |
| Flip script | `/root/scripts/flip-rag-to-int.sh` — re-pointed to rugbrain (this commit) |
| Watchdog | `migration-watchdog.timer` armed (10 min) |

## Phase 1 — FLIP (blocked on migration cursors = null)

Run `/root/scripts/flip-rag-to-int.sh --dry-run`, review, then execute. It:
validates :8004 → flips `.env.rag` (int backend, drops `_i`) → rebuilds
rag-api **from rugbrain** → fixes chat-bridge URL → re-enables bridges →
retunes embedder quota → prints verification checklist.

## Phase 2 — infra re-pointing (execute at/after flip)

| Unit / cron | Action |
|---|---|
| `rmi-rag-permanence.{service,timer}` | ✅ DONE — replaced by `rugbrain-permanence.*` (paths → rugbrain, After=rugbrain-api). Both names are the **B2 backup job**, not a serving tier |
| `rmi-rag-firehose.{service,timer}` | ✅ DONE — dead units removed (no service file; firehose cycles in-container) |
| `/etc/cron.d/rugbrain-sec-edgar` | ✅ DONE — `cd rugbrain && docker compose run rag-ingest …` |
| `rag-embed-migration.service` | ✅ disabled (old duplicate writer; migrate-embeds is canonical) |
| `migrate-embeds.service` | disable + remove AFTER migration completes (gate in flip script) |
| `mcp-codebase-ingest.service` | points at rmi-codebase-mcp (not rmi-rag) — no change |

### Old qdrant (:6333) — BLOCKED on backend, not ours

Correction (2026-08-25 audit): `rmi-backend` runs with
`QDRANT_URL=http://rmi-qdrant:6333` — the docker alias `rmi-qdrant` IS the old
container (`44b1b2f902a8_rmi-qdrant`, host port 6333). The v2 container is
`rmi-qdrant-v2` (host 6334). Decommissioning the old instance therefore
requires **rmi-backend to migrate its collections + re-point to
`rmi-qdrant-v2` first** — a backend-scope project. RugBrain itself already
talks to v2 exclusively. Do NOT stop the old container until backend moves.

### Shadow-`_i` decision (recorded)

- `news_v2_i` (~7264 pts @2048d): real unique content from the interim window
  → **backfill** post-flip via `scripts/backfill_shadow_i.py --collection news_v2_i`
- `threat_intel_v2_i` (~147 pts): same treatment, small
- all other `_i` shells (0 pts): delete outright at flip cleanup

## Phase 3 — sever the backend cord (the actual "stand alone" work)

Audited coupling (src grep 2026-08-25):

| File | Coupling | Class | Action |
|---|---|---|---|
| `firehose/news.py` | fetches news + prediction markets via `localhost:8000/api/v1/databus/fetch/*` | 🔴 HARD dep | Add direct-fetch adapters (upstream APIs the DataBus itself wraps are all free-tier: CoinGecko, DefiLlama, RSS). DataBus becomes optional enrichment, not the source. |
| `scanner_subscriber.py` | consumes `rmi:rag:ingest` redis channel fed by backend scanner | 🟡 optional input | Keep — fail-open already (subscriber waits). Standalone = runs without it, not deaf to it. |
| `scanner_feedback.py` | ingests scan results pushed by backend | 🟡 optional input | Keep, same rationale. |
| `realtime_alerts.py:877` | enqueues bot alerts drained by backend | 🟡 optional output | Keep; queue persists until drained. |
| `market_context.py` | SERVES `GET /api/v1/market/context/{addr}` TO the backend scanner | 🟢 provider role | Keep — this is RAG as a service, the healthy direction. |
| `qdrant_store.py` | write-gate: `rmi_documents` owned by backend | 🟢 boundary | Respect — never write there. |

Definition of done for "stands alone": `systemctl stop rmi-backend` (or backend
container down) → firehose still ingests (direct adapters), API serves search/
embed/context, backup snapshots run, tg-ingest cycles. Backend down degrades
scan-enrichment only.

## Phase 4 — retire rmi-rag

Only after: flip done · backup/firehose/tg-ingest restarted from rugbrain ·
one full day of green logs.
1. `systemctl disable --now migrate-embeds.service rag-embed-migration.service`
2. Archive: `mv /srv/work/repos/rmi-rag /srv/work/repos/_retired/rmi-rag-$(date +%Y%m%d)`
   (keep 30 days for the checkpoint dir reference, then delete)
3. Remove `RETIRED-DO-NOT-WORK-HERE.md` + AGENTS.md banner (they described this
   transition; historical record lives in git + this doc)
4. Sweep stragglers: `grep -r 'rmi-rag' /etc/systemd/system /etc/cron* /root/scripts`

## Verification gates

- [ ] Flip script exits 0 with all post-flip checks green
- [ ] tg_news `errors=0` (dim-mix crash gone), scam_watch ingest >0
- [ ] Channel posting resumed (WO-3 writer/score contract served by rebuilt API)
- [ ] `docker stop rmi-backend` for 10 min → firehose/API/backup unaffected
- [ ] No unit/cron/script on the box references `/srv/work/repos/rmi-rag`
