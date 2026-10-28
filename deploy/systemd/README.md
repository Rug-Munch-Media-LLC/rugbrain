# Content-daily units — three editions a day, plus a smoke unit

`deploy/systemd/` is the deploy canonical for these units; the copies here and
the installed copies under `/etc/systemd/system/` are meant to be identical
(`deploy/systemd/` is the source, `/etc` is the install).

| unit | schedule (UTC) | published title |
|---|---|---|
| `rugbrain-content-daily-morning.timer` | 06:30 | Rug Munch Intelligence — The Morning Rug |
| `rugbrain-content-daily-midday.timer` | 12:30 | Rug Munch Intelligence — Midday Market Report |
| `rugbrain-content-daily-evening.timer` | 21:30 | Rug Munch Intelligence — Evening Market Closing Report |

All three activate the same template, `rugbrain-content-daily@.service`, where
the instance name is the edition key (`morning`, `midday`, `evening`).

## Stages — one run, in this order

```
collect ──▶ score_dedupe ──▶ select ──▶ write ──▶ edit_factcheck
                                                        │
                        site_failed (exit 3) ◀── publish_site
                                                        │
                        partial     (exit 4) ◀── deliver_telegram
                                                        │
                                                    record_metrics  (always)
```

`publish_site` is fatal: if the page is not written, the channel post is never
attempted, because the teaser carries the URL. `deliver_telegram` is not fatal:
a page that is live stays live, the run reports `partial`, and the next run does
not re-post it (the ledger holds the run key) unless `--repair-delivery` is asked
for explicitly.

Exit codes (`python -m rugbrain.content_daily daily …`):

| code | meaning |
|---|---|
| 0 | published (`ok`, `repaired`) or nothing to do (`skipped`) |
| 2 | refused: `no_candidates`, `needs_review`, `writer_unavailable`, `not_due` |
| 3 | `site_failed` — nothing published, Telegram not sent |
| 4 | `partial` — page live, channel post failed |
| 1 | unexpected status (a bug, not an editorial outcome) |

A non-zero exit marks the unit failed, which is the point: `systemctl --failed`
is the content health line. `2`/`3`/`4` are editorial outcomes, not crashes.

## Install

```bash
install -m 0644 deploy/systemd/rugbrain-content-daily@.service /etc/systemd/system/
install -m 0644 deploy/systemd/rugbrain-content-daily-*.timer  /etc/systemd/system/
install -m 0644 deploy/systemd/rugbrain-content-daily-smoke.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rugbrain-content-daily-morning.timer \
                        rugbrain-content-daily-midday.timer \
                        rugbrain-content-daily-evening.timer
systemctl list-timers 'rugbrain-content-daily-*'
```

The bridge URL lives outside git:

```bash
install -m 0600 /dev/null /srv/secrets/rugbrain-content-daily.env
printf 'RUG_CONTENT_TELEGRAM_URL=http://127.0.0.1:PORT/content/telegram\n' \
  > /srv/secrets/rugbrain-content-daily.env
```

Without it the run reaches `deliver_telegram`, fails closed with
`DeliveryNotConfigured`, and exits 4 — the page is already published, nothing is
guessed.

## Run one edition by hand

```bash
cd /srv/work/repos/rugbrain

# render only: no site write, no channel post
.venv/bin/python -m rugbrain.content_daily daily --edition morning --dry-run --json

# a specific UTC day, before its slot (timers never need --force)
.venv/bin/python -m rugbrain.content_daily daily --edition midday --date 2026-10-29 --force

# re-send only the channel card for a run whose page already exists
.venv/bin/python -m rugbrain.content_daily daily --edition evening --repair-delivery

# no network at all: proves the CLI, the stages and the payload renderer
.venv/bin/python -m rugbrain.content_daily daily --edition morning --offline --dry-run --force --json

# the same run through systemd
systemctl start rugbrain-content-daily-smoke.service
journalctl -u rugbrain-content-daily-smoke.service -n 20 --no-pager
```

Output is one JSON envelope on stdout with `--json` (lane diagnostics go to
stderr), otherwise a short block plus the health line:

```
content-daily health: morning=ok@2026-10-29T06:30:04Z https://rugmunch.io/story/… | midday=never | evening=never
```

## Rollback

```bash
# stop the schedule, keep the files
systemctl disable --now rugbrain-content-daily-morning.timer \
                          rugbrain-content-daily-midday.timer \
                          rugbrain-content-daily-evening.timer

# remove the install (the repo copies in deploy/systemd/ stay)
rm /etc/systemd/system/rugbrain-content-daily@.service \
   /etc/systemd/system/rugbrain-content-daily-*.timer \
   /etc/systemd/system/rugbrain-content-daily-smoke.service
systemctl daemon-reload
```

Nothing is un-published by a rollback: the site pages and the ledger rows are
the record of what went out. `data/content/daily-ledger.jsonl` is what stops a
re-enabled timer from re-posting an edition that already shipped. To retry one
edition deliberately, use `--date` + `--repair-delivery` on that edition — do
not delete ledger rows to force a run.

## Known limits

- `Persistent=true` means a missed slot fires at boot. A `morning` run at 13:00
  UTC will still publish a brief titled *The Morning Rug*; the pipeline only
  refuses a **future** slot, not a late one. [REVIEW] if late catch-up should
  become `skipped`.
- Timers are scheduled in UTC (`OnCalendar=… UTC`) while the box clock is CET.
  `systemctl list-timers` renders `NEXT` in local time.
