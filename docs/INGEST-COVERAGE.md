# INGEST COVERAGE — rugbrain, measured 2026-10-25 (Talos, read-only + bounded runs)

Method: systemd timer state (`systemctl list-timers --all`), unit journals,
Qdrant `/collections/*/points/count`, ClickHouse `rmi_articles`, Postgres `news_items`.
"No rows" claims below are measured counts, not assumptions.

## Source → configured → last run → rows → verdict

| Source | Configured | Enabled / last run | Store | Rows (cumulative) | 7d | Verdict |
|---|---|---|---|---|---|---|
| RSS news | 156 feeds (`config/rss_feeds.txt`) + 192 candidates + 8 `security_feeds.txt` | no dedicated RSS timer — reached only via consolidate/scam lanes | Qdrant `news_articles_8b` | 2,451 | n/a | **STARVED** — 164 live feeds, no serving timer |
| RSS → ClickHouse | same | — | CH `rmi_articles` | **0** | 0 | **SILENT (dead sink)** |
| News stories | — | hourly `rugbrain-consolidate` (ok, 11:04) | Qdrant `news_v2` | 24 | 23 (`updated_at`) | THIN — 24 stories is not a news desk |
| News corpus (8B) | — | 11:04 consolidation ok | Qdrant `news_v2_8b` | 299,069 | n/a | **NOISY** — 299k points vs 24 stories ≈ 12.5k chunks/story; chunk inflation or misplaced channel [REVIEW] |
| X / Twitter | 500 handles (`config/x_registry.json`), 22 in `x_handles.json` lane | `rugbrain-x-ingest.timer` **ok**, 11:31 | Qdrant `social_intel_8b` | 22,400 | active | **OK (path proven)** |
| Telegram — accountless | 250 channels (`t.me/s`) | `rugbrain-tgweb.timer` **ok**, 11:39 | Qdrant `social_intel_8b` | 22,400 (+8 today) | +8 | **OK** |
| Telegram — user account | `data/telegram_candidates.json` | `rugbrain-tg-ingest` dead; `tg-alt-ingest` **FAILED 11:16**; `tg-insight-ingest` **FAILED 11:32** | — | **0** | 0 | **SILENT (auth)** — see root cause |
| Telegram — scam watchlist | 140 channels/run | `rugbrain-scam-watch` **FAILED 11:22** | — | 0 | 0 | **SILENT (same auth)** |
| Reddit | not configured (no feed/handler found) | — | — | 0 | 0 | **NOT WIRED** |
| Papers / research | `config/corpus_players_manifest.json` | manual / permanence | Qdrant `research_v2_8b` | 670 | n/a | STALE-ish, no timer |
| OSV / advisories | not configured | — | — | 0 | 0 | **NOT WIRED** |
| OFAC / sanctions | not configured | — | — | 0 | 0 | **NOT WIRED** |
| Regulation / enforcement | security feeds | consolidation | `regulation_enforcement_8b` | 5,352 | n/a | OK |
| Contract audits | manifest | — | `contract_audits_8b` | 1,050 | 0 | STATIC (one-off backfill) |
| Exploits | — | — | `exploits_8b` | 1,344 | **0 (30d: 0)** | **SILENT** — no refresh |
| Known scams | watchlist | partial | `known_scams_8b` | 27,300 | n/a | bulk-loaded, not refreshed |
| Live threats | — | — | `live_threats` | **0** | 0 | **SILENT (empty collection)** |
| OHLCV / market | coingecko/defillama pollers (`alerts/poller_*`) | alert loop | Qdrant `market_data` | 1,062 | n/a | alerts only, not a corpus |
| News (Postgres) | — | — | PG `news_items` | 13,902 | n/a | live, but not the retrieval store |

## Telegram answer (explicit)
- Configured: 250 accountless channels + 17 brand `telegram_handle`s in `company_channels.json`
  + `telegram_candidates.json` (join/candidate list).
- Ingested: **only via the accountless `t.me/s` lane** (`rugbrain-tgweb`, 250 channels,
  68 msgs → 6 unique → 8 points, 61 gated as noise).
- **Not ingested: all account-based channels.** Three units fail identically:
  `telethon.errors.rpcerrorlist.AuthKeyDuplicatedError` — the same `.session` file is used
  from two IPs at once, so Telegram has permanently invalidated that auth key.

## X answer (explicit)
- Path: `rugbrain-x-ingest.service` (GetXAPI + Pry) → Qdrant `social_intel_8b`, via
  `config/x_registry.json` (500 handles) and the 22-handle `config/x_handles.json` seed lane.
- Last run 11:31 today, timer healthy → **X IS ingested**, same collection as Telegram.
- Row count: 22,400 cumulative in `social_intel_8b`; today's telegram run took it 22,400 → 22,408.

## Silent / noisy
- **Configured but silent:** `rmi_articles` (0 rows), `live_threats` (0), Telegram account
  lanes (3 units failed), `exploits_8b` (0 rows in 30d), Reddit/OSV/OFAC (not wired at all).
- **Noisy / low dedupe:** `news_v2_8b` 299,069 points for 24 stories; `known_scams_8b` 27,300
  bulk points; `rag_documents` 730,558 with only 7,053 touched in 7d.

## Root cause of the biggest silent gap
`AuthKeyDuplicatedError` on one shared Telethon session. Recovering the accounts needs a
fresh login (phone + OTP) — **HITL**, cannot be automated here. Interim coverage is the
accountless lane, proven working. Durable fix: one session file per unit (separate
`TELEGRAM_SESSION` per service) so concurrent runs can never reuse a key again.

## Verified delta
`rugbrain-tgweb` restarted at 11:39 → `channels=250 messages=68 unique_docs=6 ingested=8`
→ `social_intel_8b` 22,400 → 22,408 (+8). Ingest path works end to end; the gate discarded 61 noise messages.
