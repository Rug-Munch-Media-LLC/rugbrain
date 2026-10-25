# YouTube Scam Watch

Bounded monitoring of crypto YouTube channels for promoted tokens. Output is an
**article brief** for the content pipeline — never a publication. Every brief is
`draft`, framed as `alleged`, marked `[REVIEW]`, and carries legal-safety notes.

## Watchlist schema (`config/youtube_watchlist.json`)

| Field | Type | Notes |
| --- | --- | --- |
| `name` | str | Channel display name |
| `handle` | str | `@handle`, unique, case-insensitive |
| `channel_url` | str | `https://www.youtube.com/...` only |
| `category` | str | Editorial category, not a risk rating |
| `cadence` | enum | `daily` / `weekly` / `biweekly` / `monthly` / `ad_hoc` |
| `subs` | int \| null | Seeded `null`; no unsourced numbers |
| `subs_source` / `subs_checked_at` | str / datetime \| null | Provenance for `subs` |
| `last_checked` | datetime \| null | Set by the scheduler after a run |
| `risk_history` | list | `{at, kind, reference, summary}`, append-only |
| `notes` | str \| null | Free text, e.g. "baseline monitoring" |

Seeded with 14 public channels whose handles already exist in
`src/rugbrain/youtube_ingester.py` (verified 2026-08-18). A seeded entry means
"watched", never "suspected".

## Signals (`signals.py`)

| Signal | Weight | Trigger |
| --- | --- | --- |
| `contract_address` | 0.35 / 0.20 | EVM or Solana mint in title/pin (0.35) or description (0.20) |
| `guarantee_language` | 0.20 | "guaranteed", "risk free", "100x", "to the moon", … |
| `presale_language` | 0.15 | presale / whitelist / ICO-style sale terms |
| `urgency_language` | 0.05 | "last chance", "minutes left", … |
| `missing_disclosure` | 0.25 | promo terms present, no label and no disclosure marker |
| `new_token` | 0.20 | token launched ≤72 h before the video |
| `holder_concentration` | 0.25 | top-10 holders ≥50% of supply |
| `honeypot_flags` | 0.40 / 0.20 | honeypot flag, or buy/sell tax >10% |
| `deployer_linkage` | 0.35 | deployer linked to a known bad actor |

Sum is capped at 1.0. It is a triage score, not a probability of wrongdoing.

## Verification (`verify.py`) — all four rules must hold

1. **Two independent sources**: distinct registrable domains, not two URLs from
   one publisher.
2. **At least one on-chain row**: token risk, holders, deployer or funding data.
3. **Evidence is dated and ≤30 days old** at verification time.
4. **Score ≥0.50 and confidence ≥0.60**.

Anything else yields `publishable=False` with reason codes
(`fewer_than_two_independent_sources`, `no_onchain_evidence`, `stale_evidence:N`,
`insufficient_signals`, `confidence_below_floor`). The brief builder raises
`InsufficientEvidence`; there is no force path.

Evidence rows: `{kind: onchain|platform|video_metadata|third_party_report, url,
source, captured_at, claim, confidence}`. Capture timestamps are mandatory —
an undated row cannot be counted, so it cannot enter a brief.

## Provider adapters

`run_scan` takes injected fetchers, so the transport is not baked in:

* `fetch_videos(channel) -> [VideoMeta]` — public watch pages / yt-dlp metadata
  (no login, no private endpoints, rate limit between channels).
* `fetch_evidence(address, chain) -> [EvidenceItem]` — map `rugmunch-data`
  token/holder/deployer responses and `context-crypto.threat_intel` output onto
  evidence rows. **Fail closed**: if the provider errors (e.g. RugBrain HTTP 500
  observed 2026-10-25) or `rugmunch-data` errors (internal shape error observed
  2026-10-25), the row is absent and the verdict refuses — a provider outage
  never produces a publishable brief. Adapters are injected, so neither tool is
  on the import path and a test proves the fail-closed path.

ContextCrypto's curated security corpus (`query_security_context`) supplies the
pattern checks behind honeypot/tax/concentration claims; cite it as a source
only for the pattern, not as evidence about a specific token.

## Bounds and ethics

* `MAX_CHANNELS_PER_RUN = 5` (hard `ValueError` above it).
* `MIN_SECONDS_BETWEEN_FETCHES = 6.0`, injected sleeper so tests assert throttle.
* Public data only; no login, no paywalled or private endpoints; platform terms
  respected; one failing channel is recorded in `errors` and skipped.
* Repeat offenders are marked (`repeat_offender`, `prior_reports`) for the
  editor; the same video is never briefed twice.

## Pipeline contract

`ArticleBrief` = `{slug, headline (≤70 chars), dek, channel, handle, video_id,
video_url, status="draft", framing="alleged", confidence, signals, evidence,
timeline, legal_notes, prior_reports, repeat_offender, review="[REVIEW]",
generated_at}`. The content pipeline may schedule an editorial review from it;
it must not publish it without a human sign-off and a right-of-reply attempt.
