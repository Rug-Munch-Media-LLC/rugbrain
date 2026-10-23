# Telegram channel discovery (scam / shill / promo)

Two discovery directions, one registration path.

| Direction | Module | Joins? | Cost |
| --- | --- | --- | --- |
| Handles we already joined (dialog list) | `discover_joined_channels.py` | never | 0 API calls (inventory snapshot) |
| Handles guessed from the open web | `discover_scam_channels.py` | yes, ≤4/run, 45–120s jitter | MTProto |

Both register through `channel_registry.append_channels`, which is the only
writer of `/var/lib/rugbrain/tg_channels.txt`: case- and `@`-insensitive dedupe,
returns exactly what was added, so every run is safe to repeat.

## What counts as a scam channel

`joined_classifier.classify_joined(title, handle)` returns
`scam_caller | shiller | security | promotion | None`.

- taxonomy hits (`channel_taxonomy.categorize`) decide the first three, so brand
  channels such as `CertiKAlert` keep working;
- honeypot / rug / drainer wording falls into `scam_caller`;
- Telegram's shell-channel naming (`… Portal`, `… Announcements`, `Traffic Ad`)
  is `promotion`;
- official venues (`binance_announcements`, `arbitrum`, `celestiaorg`,
  `spotonchain`) are news, not promo;
- **our own channels are never classified** (`cryptorugmunch*`, `rmialpha`,
  `rmialerts`, `munchscans`).

Provenance of every registered scam handle lives in
`scam_channels_pinned.py` (`ACTIVE` = posted within 180 days, `DORMANT` =
public but quiet, `NO_PUBLIC_PREVIEW` = classified but unreadable).

## Public ingestion needs no login

`telegram_web.py` sweeps `https://t.me/s/<handle>` — plain HTML, no auth, no
session, no ban risk. A handle is only registered after that URL returns 200
**and** renders a `tgme_widget_message`; a 200 with an empty body means the
channel has no public preview and the sweeper would log nothing forever.

Probe result 2026-10-24: 20 of `discover_scam_channels.CANDIDATES` guesses
yield 200-with-no-messages. Do not add handles from that list without probing.

## Dedupe

`ingest_cycle.gate_and_bound` collapses equal content hashes **within** a cycle
as well as across cycles. The hash is content-only, so one text mirrored by
five shill channels is one document with `echo_count = 5` (the shill-campaign
signal), not five documents and five vector calls. The count is reported as
`cross_channel_duplicates`.

## Session blocker (2026-10-23)

`rugbrain-tg-alt-ingest` and `rugbrain-tg-insight-ingest` both fail at
`client.connect()`:

```
telethon.errors.rpcerrorlist.AuthKeyDuplicatedError: The authorization key
(session file) was used under two different IP addresses simultaneously, and
can no longer be used.
```

An auth key in that state is dead permanently. **Do not retry, and do not add
a third reader** — a fresh reader on a duplicated key just duplicates it again.
Operational rule:

1. Sweep public channels with `telegram_web` (accountless) — preferred default.
2. Read the dialog list from the inventory snapshot (`discover_joined_channels`,
   no connection) instead of opening the session.
3. `--live` is a single bounded `iter_dialogs`, one process per account, and it
   raises `DuplicateSessionError` rather than retrying.
4. Re-authorising alt/insight needs a new session file from the operator, on
   its own egress IP (`TELEGRAM_PROXY_*`); not a code change.

## Run

```bash
python -m rugbrain.ingest_sources.discover_joined_channels          # report
python -m rugbrain.ingest_sources.discover_joined_channels --write   # register verified
```
