# Prompt pack provenance — which rule came from which skill

Every rule in this directory is traceable to a loaded skill or to the canonical
style config. A rule without a source does not belong here.

| Rule (where it appears) | Source |
|---|---|
| Every factual sentence carries an inline `[n]` that resolves to a note | `editorial-standards` #11 (every claim needs a source), #21 (corroborate with 2+ sources) |
| On-chain data over social posts; no unverified claim as fact | `editorial-standards` #12 |
| Most important fact first; lede answers who/what/when/where/why | `editorial-standards` #6 (inverted pyramid), #7 (lede) |
| 2–4 sentences per paragraph; no walls of text | `editorial-standards` #8 |
| Headline under 70 chars, sentence case, active voice, one fact | `editorial-standards` #9 |
| Authoritative, not arrogant; no hype, no FUD, no mockery of victims | `editorial-standards` #16, #17, #18, #19 |
| No price prediction, no return forecast, no financial advice | `editorial-standards` anti-patterns list |
| No "approximately/around" before a figure; exact block/timestamp/amount | `editorial-standards` #25 (timeline rigor) |
| Allegations attributed, never stated as fact | `editorial-standards` #24 (right of reply), #13 (anonymous sources) |
| One image, alt text + credit, sourced from a candidate only | `editorial-standards` verification checklist ("images have alt text + source attribution") |
| Thin evidence → `insufficient_evidence`, never a padded edition | `editorial-standards` #11 + the `daily_rug` / `daily_rug_publish` status contract |
| PnL reported as realised outcome per call, never a performance claim | `editorial-standards` #17, #18 (no hype / no FUD symmetry) |
| Banned phrases, budgets, tickers, number formats read from `config/content_style.json` instead of restated here | `stop-slop` #9 (no magic numbers), #20 (no duplicate text), one canonical truth |
| Prompt text lives in files, loaded by one loader; every module <200 lines | `stop-slop` #17/#18, fleet coding canon |
