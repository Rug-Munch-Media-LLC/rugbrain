# Corpus usability — measured, not sized

Method: for each served `_8b` collection, scroll 10 points, read the primary text field,
and ask the only two questions our consumers actually pose:
**"how does X work / fail"** and **"is Y a scam"**.

Score: `2` = states a mechanism, a category-with-reason, or a labelled good/bad code
reference (answers the question). `1` = an identifier/label/fact lookup only (answers
"is this exact address/domain known", never "how"). `0` = cannot answer either class.

**usable knowledge = score x points.** Raw size is not knowledge.

## Ranking (by usable knowledge)

| # | Collection | Points | Score | Usable | Verdict |
|---|---|---:|---:|---:|---|
| 1 | scam_intel_v2_8b | 119,269 | 1 | 119,269 | lookup-only, 3x duplicate of known_scams |
| 2 | known_scams_8b | 42,611 | 1 | 42,611 | lookup-only, bulk domain dumps |
| 3 | security_patterns_8b | 14,275 | 2 | 28,550 | best content in the corpus; under-wired |
| 4 | defi_exploits_8b | 16,713 | 1 | 16,713 | PoC code mid-snippet, no mechanism label |
| 5 | scam_patterns_8b | 8,080 | 2 | 16,160 | URL + Category + addresses: answerable |
| 6 | live_threats_8b | 11,164 | 1 | 11,164 | whale-swap telemetry, not threats |
| 7 | security_feeds_8b | 874 | 2 | 1,748 | real SlowMist write-ups, mid-article chunks |
| 8 | exploits_8b | 1,344 | 1 | 1,344 | rug-pull liquidity stats, label-only content |
| 9 | contract_security_8b | 1,192 | 1 | 1,192 | raw Solidity windows, no labels |
| 10 | contract_audits_8b | 1,050 | 1 | 1,050 | PDF slices + Trail of Bits CSV rows |
| 11 | github_audits_8b | 839 | 1 | 839 | whole-document chunks (blocker #1) |
| 12 | security_findings_8b | 166 | 2 | 332 | named `.sol` per chunk: highest per-point value |
| 13 | market_data | 1,062 | 0 | 0 | TVL snapshots, no mechanism |
| 14 | market_health_8b | 636 | 0 | 0 | OI leaders, no mechanism |
| 15 | threat_intel_v2_8b__sparsemig | 196,189 | 0 | 0 | screenshot OCR noise ("verify you are human") |
| — | exploits / live_threats | 0 | — | 0 | empty shells |

**Headline:** the largest collection in the store (196,189 points) contributes zero
usable knowledge, while the 166-point `security_findings_8b` contributes 332. Size and
usefulness are anti-correlated here. Reachable knowledge is ~10% of points; *usable*
knowledge is ~3%.

## Top 3 usable-but-under-wired

1. **security_patterns_8b** (28,550 usable, score 2). Blocks use: payload carries
   `title`/`description`/`category` but **no `mechanism_tag`** and no `class`, so a
   mechanism query cannot filter or rerank on it; and the served name resolves through
   `_DECLARED_TWINS` while consumers ask `security_patterns` (636 points, advisories
   only) — a caller hitting the short name silently gets 4% of the content.
2. **github_audits_8b** (839 usable, score 1 — *should* be ~25k). Blocks use:
   **document-level chunking**. One point = one whole PDF/repo, mean 1,933 chars,
   spanning a link table, an exploit PoC and a Next.js README. A mechanism question
   retrieves the README. No `title`, no `mechanism_tag`. Also the hybrid twin holds
   **199** points vs 839 in the base — the twin is 24% wired.
3. **scam_patterns_8b** (16,160 usable, score 2). Blocks use: no `mechanism_tag`; the
   category lives inside prose (`Category: Phishing (MyEtherWallet)`) instead of a
   filterable field, and `Description` is empty on sampled rows. Answerable, not
   routable.

## Retirement candidates (retire, never delete)

- **threat_intel_v2_8b__sparsemig** (196,189 pts, score 0) — OCR of social screenshots;
  sampled text is duplicate spam. Keep the Parquet source, drop the vectors.
- **market_data** (1,062) and **market_health_8b** (636) — out of scope for the
  security question classes; a live API answers these better than a stale snapshot.
- **exploits** and **live_threats** — 0 points. Empty shells; removing them from the
  registry stops callers routing to nothing.

## The fix applied (cheapest high-impact): finding-level re-chunk

`scripts/rechunk_audit_findings.py` splits a stored document at finding markers
(`[H-01]`, `M-03`, `1. ...`), falling back to paragraph windows, and derives the payload
fields a mechanism question needs: `title`, `mechanism_tag` (14-tag taxonomy),
`class`, `has_code`, `source_ref`. Dry-run by default; `--ask` demonstrates retrieval.

Before — `github_audits_8b`, question *"how does the reentrancy in the withdraw function work"*:
top payload keys are `content, metadata, source, severity, chain, collection`; no `title`,
no `mechanism_tag`; sampled text is a link table / Next.js README.

After (chunk level, run in this worktree) — see the `chunks=/tagged=/with_code=` line and
the `ASK:` block in the job log. Remaining wiring (not done here, `[REVIEW]`):
upsert with vectors from the 8B lane, then expose `security_patterns(question)` that
filters `mechanism_tag` before dense search, and point it at `github_audits_8b` +
`security_patterns_8b` + `security_findings_8b`.

## Three questions, before / after

| Question | Before (current corpus) | After (finding-level chunks) |
|---|---|---|
| How does the reentrancy in `withdraw` work? | Whole-document chunk; no mechanism label; README/link-table text wins. | A finding chunk tagged `reentrancy`, with `function`/`.sol` reference and `has_code=true`. |
| Is `0xb262...7d77` a scam? | `known_scams_8b`: "Etherscan scam label ... Fake_Phishing4645" — answers yes, no reason. | Unchanged (lookup class); `scam_patterns_8b` adds `Category:` as a filterable field. |
| How does the oracle manipulation in this lending market work? | `defi_exploits_8b` returns a mid-snippet PoC with `attack_vector` on 8/10 rows only. | Finding chunk with `mechanism_tag=oracle-manipulation` + code ref; filterable before rerank. |

## Next actions

1. Upsert the re-chunked findings into a `github_audits_findings_8b` collection (new
   name, so the old twin keeps serving until parity is proven).
2. Backfill `mechanism_tag` on `security_patterns_8b` from `title` + `category`.
3. Fix the hybrid twin drift (199 vs 839) — `docs/DIM-DRIFT-GATE.md` exists for this.
4. Retire the two zero-point shells and unregister `threat_intel_v2_8b__sparsemig`.
