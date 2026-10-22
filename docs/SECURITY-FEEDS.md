# Security-feed web paths (probe 2026-10-23, Talos)

Gap this closes: the Telegram **public-preview** lane cannot read channels whose
preview is closed, and several security desks publish on their own sites rather
than in a feed we already carry. Paths below were probed with a real GET
(`status` + parsed `<item>`/`<entry>` count) from Talos; nothing here is
assumed. X is **not** fetched by this lane — `rugbrain.ingest_sources.x_intel`
already owns X (GetXAPI + Pry, writes Qdrant `x_social`, paid).

Ingest: `scripts/ingest_security_feeds.py` → `news_items`, `source_trust = 0.9`,
category from `rss_classify.classify` (exploit / scam where the text says so),
dedupe on `news_id` **and** `content_hash`.

## Working paths (enabled in `config/security_feeds.txt`)

| Source | Path that worked | Kind | Probe | Newest item |
|---|---|---|---|---|
| SlowMist | `slowmist.medium.com/feed` | rss | 200, 10 items | 2026-10-20 |
| PeckShield | `peckshield.medium.com/feed` | rss | 200, 10 items | **2021-08-15 (archive)** |
| CertiK | `certik.medium.com/feed` | rss | 200, 10 items | 2026-09-08 |
| Chainalysis | `chainalysis.com/blog/feed/` | rss | 200, 10 items | 2026-10-05 |
| Wu Blockchain | `wublockchain.medium.com/feed` | rss | 200, 10 items | 2026-10-22 |
| Jupiter Research | `jupresear.ch/latest.rss` | rss | 200, 30 items | 2026-09-21 |
| CoinDesk | `coindesk.com/arc/outboundfeeds/rss/` | rss | 200, 25 items | 2026-10-22 |
| BlockSec | `blocksec.com/sitemap.xml` | sitemap | 200, 3870 `/blog/` locs | 2026-10-22 |

Counts from the first full run (`--cap 20`): SlowMist 7, PeckShield 10, CertiK 10,
Chainalysis 7, Wu Blockchain 10, Jupiter Research 20, BlockSec 20 — 84 new rows;
CoinDesk 0 new (20/20 already present from the main RSS cron → dedupe proof).
Re-run appended **0** (110 seen / 110 duplicate).

**PeckShield caveat:** its Medium feed is frozen at Aug 2021. The path is a real
feed and a useful backfill, but live PeckShield intel is X-only (already covered
by `x_intel`). Do not read a PeckShield post from `news_items` as current.

**BlockSec caveat:** the sitemap mixes locales (`/ko-kr/blog/…`, `/pt-br/…`,
`/zh-hant/…`). Only bare `^/blog/<slug>$` paths are ingested; the localized
duplicates that a first pass created were deleted (15 rows) and the parser
anchored so they cannot return.

## Blocked / no free web feed

| Source | What was tried | Result | Note |
|---|---|---|---|
| Dune | `dune.com/blog/feed` | 403 Cloudflare to this host | FlareSolverr path exists (pry stack `:8192`) |
| Messari | `messari.io/feed`, `/rss`, `/sitemap.xml` | 429 rate wall | needs backoff or a key |
| Nansen | `/research/feed`, `/rss`, sitemap | 404 / no article locs; page is a Next.js payload | needs listing scraper or app API |
| DefiLlama | `/feed`, `/blog`, sitemap | 404 feed; sitemap carries data pages only | `/research` HTML is client-rendered |
| ScamSniffer | `scamsniffer.io`, `drops.`, Medium | no feed, no sitemap | **X only** (@realScamSniffer) |
| MistTrack | `misttrack.io/blog`, sitemap | 404 / no article locs | **X only** (@MistTrack_io) |
| Lookonchain | `/rss`, sitemap, `/articles/` | 404 / JS-rendered | **X only**, and the closed-preview TG mirror already covers the channel |
| Hyperliquid | `hyperliquid.xyz`, sitemap, gitbook | 403 site+sitemap; docs sitemap = product docs | **X only** + docs |
| Drift | `/blog/rss`, sitemap | 404 feed; sitemap has no dated articles | blog HTML needs a listing scraper |
| SEAL 911 | `seal.org`, `securityalliance.org` | no news feed | already ingested from GitHub `security-alliance/seal-911` (`seal911.py`) |

## Needs a paid API (no free path that respects ToS)

* **Lookonchain, MistTrack, ScamSniffer, Hyperliquid** — X-only; X scraping
  without the paid lane breaks ToS and gets rate-walled. Live coverage already
  flows through `x_intel` (GetXAPI budget, `XINTEL_*` caps in the unit drop-in).
* **Nansen, Messari** — their research endpoints are product APIs; the public
  pages are JS payloads. A key (or the sitemap-with-backoff recovery) is the only
  durable route.

## Runbook

```bash
cd /srv/work/repos/rugbrain
set -a; . /srv/secrets/backend.env; set +a
.venv/bin/python scripts/ingest_security_feeds.py --dry-run            # fetch, roll back
.venv/bin/python scripts/ingest_security_feeds.py --only BlockSec      # one source
.venv/bin/python scripts/ingest_security_feeds.py --json /tmp/sec.json # per-source report
```

`--dry-run` runs every fetch and insert inside a transaction that is rolled back,
so it proves reachability without touching the table.
