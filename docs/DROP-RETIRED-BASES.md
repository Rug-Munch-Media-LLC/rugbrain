# Runbook — dropping the retired 4B/2560 + 1024 Qdrant collections

**Status: NOT RUN. Nothing has been deleted.** This is the validated tool and
the measured plan. Executing is a separate, human decision.

**Tool:** `scripts/drop_retired_bases.py` — dry run by default.
**Target:** Qdrant `:6334` (production). The dry run issues GET only.

## The single command

```bash
cd /srv/work/repos/rugbrain
python3 scripts/drop_retired_bases.py \
  --empty-shell audit_findings   --empty-shell coding_knowledge \
  --empty-shell does_not_exist  --empty-shell exchange_flows \
  --empty-shell hyperliquid     --empty-shell liquidation_events \
  --empty-shell scams_v2        --empty-shell scanner_memory \
  --empty-shell solana_patterns --empty-shell token_unlocks \
  --empty-shell wallet_clusters --empty-shell youtube_videos
```

That is the dry run: it prints the full plan and deletes nothing. To actually
delete, both flags are required — `--execute --yes`. `--execute` alone exits 1
and deletes nothing.

## The assertion

A retired base is deleted **only if** its `_8b` twin exists and
`twin_points >= base_points`, read live from Qdrant. The rule is applied twice:

1. **Plan time** — every candidate is scored and shown with its delta.
2. **Delete time** — base, twin and width are re-read immediately before each
   `DELETE`. The earlier verdict is treated as a hypothesis, never as a fact.

Step 2 exists because live ingesters are still writing the twins. Point counts
move while the run is in flight; a twin that was ahead at plan time can be
behind at delete time. Measured during development: `education` moved from
`3471` to `3497` twin points and `scan_intel` from `52360` to `52445` across
consecutive dry runs minutes apart.

If the assertion fails for any collection, that collection is **skipped and
reported**, and the run continues. Exit code is 1 while any candidate fails.

## What the assertion does NOT prove

`twin_points >= base_points` compares **counts, not membership**. It proves the
twin holds at least as many points. It does **not** prove the twin holds the
same points. A base that received writes the twin never received would pass this
assertion while being genuinely unrecoverable. Content-level equivalence was
not checked here and must not be inferred from a point count.

## Deletion is irreversible for the delta

For any collection whose twin is **behind**, deleting the base destroys points
the twin does not hold. There is no undo, no snapshot of the base itself, and no
trash. B2 does not back the 2560 bases up (see below), so a deleted base whose
twin is behind is **gone**. The tool refuses to create that situation rather
than leaving it to the operator to notice.

## Recovery position — verified per collection

B2 bucket `rmi-rag-permanence`, `snapshots/2026-09-29/`, read with
`b2 ls` using the read-only keys `gopass rmi/infra/b2_s3_key_id` and
`gopass rmi/infra/b2_s3_application_key`. **58 snapshot days verified present,
2026-08-01 → 2026-09-29** (the brief said 63; the measured count is 58).

B2 backs up the **4096 canonical set and the empty shells only**. The 2560
bases have no B2 object: `codebase_reference_v2.parquet` is **absent** from the
snapshot, and every 2560 base is absent. An empty shell's object is 787 bytes,
which is an empty parquet — correct, since the collection holds 0 points.

**So for the 26 Group A collections, the `_8b` twin IS the recovery path, and I
verified each twin's parquet object is non-trivial** (smallest: `hacks_v2_8b` at
115,090 bytes). Per-collection:

| base | dim | base pts | twin | twin pts | twin parquet bytes (B2) |
|---|---:|---:|---|---:|---:|
| `contract_audits` | 2560 | 902 | `contract_audits_8b` | 902 | 10,853,970 |
| `contract_security` | 2560 | 1,099 | `contract_security_8b` | 1,099 | 14,392,144 |
| `cross_chain` | 2560 | 13 | `cross_chain_8b` | 13 | 190,732 |
| `crypto_vulnerabilities` | 2560 | 4,918 | `crypto_vulnerabilities_8b` | 4,918 | 61,422,613 |
| `defi_exploits` | 2560 | 16,648 | `defi_exploits_8b` | 16,648 | 209,756,058 |
| `exploits` | 2560 | 1,344 | `exploits_8b` | 1,344 | 16,848,132 |
| `github_audits` | 2560 | 199 | `github_audits_8b` | 199 | 2,853,275 |
| `hacks_v2` | 2560 | 8 | `hacks_v2_8b` | 8 | 115,090 |
| `known_scams` | 2560 | 26,720 | `known_scams_8b` | 26,720 | 337,932,586 |
| `live_threats` | 2560 | 11,164 | `live_threats_8b` | 11,164 | 137,343,172 |
| `market_health` | 2560 | 627 | `market_health_8b` | 627 | 7,741,178 |
| `master_skills` | 2560 | 4,495 | `master_skills_8b` | 4,495 | 55,187,490 |
| `news_articles` | 2560 | 2,371 | `news_articles_8b` | 2,371 | 29,610,626 |
| `news_v2` | 2560 | 161,054 | `news_v2_8b` | 166,383 | 2,074,713,638 |
| `protocol_design` | 2560 | 58 | `protocol_design_8b` | 58 | 882,205 |
| `rag_documents` | 2560 | 534,076 | `rag_documents_8b` | 535,452 | 13,776,155,788 |
| `regulation_enforcement` | 2560 | 5,081 | `regulation_enforcement_8b` | 5,081 | 53,426,915 |
| `research_v2` | 2560 | 158 | `research_v2_8b` | 158 | 2,264,202 |
| `scam_intel_v2` | 2560 | 107,878 | `scam_intel_v2_8b` | 107,878 | 1,316,575,389 |
| `scam_patterns` | 2560 | 7,102 | `scam_patterns_8b` | 7,102 | 87,389,939 |
| `security_patterns` | 2560 | 2,012 | `security_patterns_8b` | 2,012 | 25,579,791 |
| `social_intel` | 2560 | 17,286 | `social_intel_8b` | 20,980 | 253,713,605 |
| `stablecoin_market` | 2560 | 336 | `stablecoin_market_8b` | 336 | 4,327,387 |
| `threat_intel_v2` | 2560 | 44,051 | `threat_intel_v2_8b` | 44,118 | 532,022,536 |
| `wallet_profiles` | 2560 | 40,355 | `wallet_profiles_8b` | 40,355 | 494,520,103 |
| `wallet_v2` | 2560 | 9,657 | `wallet_v2_8b` | 9,657 | 118,103,989 |

**Not verified:** I did **not** compare point IDs or payload content between any
base and its twin, so "twin covers base" means the count comparison passed and a
backup object exists — nothing stronger.

## Group A — 26 retired bases whose twin covers them

Reclaimable on this run: **999,685 points** (measured; it drifts upward while
ingesters run).

## Group B — 12 empty shells, 0 points, no twin to assert against

| collection | dim | live pts | B2 parquet bytes |
|---|---:|---:|---:|
| `audit_findings` | 2560 | 0 | 787 |
| `coding_knowledge` | 2560 | 0 | 787 |
| `does_not_exist` | 2560 | 0 | 787 |
| `exchange_flows` | 1024 | 0 | 787 |
| `hyperliquid` | 1024 | 0 | 787 |
| `liquidation_events` | 1024 | 0 | 787 |
| `scams_v2` | 2560 | 0 | 787 |
| `scanner_memory` | 2560 | 0 | 787 |
| `solana_patterns` | 2560 | 0 | 787 |
| `token_unlocks` | 1024 | 0 | 787 |
| `wallet_clusters` | 1024 | 0 | 787 |
| `youtube_videos` | 1024 | 0 | 787 |

These have no data at risk: 0 live points, and the B2 object is an empty
parquet. The tool re-reads each and refuses any that has silently accumulated
points.

## Refused / skipped — 7 collections, 55,642 points NOT reclaimed

The assertion refused these. They stay.

| collection | base pts | twin pts | short by |
|---|---:|---:|---:|
| `scan_intel` | 52,445 | 0 | 52,445 |
| `codebase_reference_v2` | 2,131,274 | 2,126,630 | 4,644 |
| `education` | 4,833 | 3,497 | 1,336 |
| `security_feeds` | 874 | 842 | 32 |
| `agent_vectors` | 182 | 0 | 182 |
| `security_alerts` | 125 | 119 | 6 |
| `security_patterns_test` | 92 | 89 | 3 |

`scan_intel` and `agent_vectors` have twins at **0 points** — the twin is an
empty shell, not a lagging copy. `codebase_reference_v2` is the single largest
collection on the host and is **4,644 points behind**; it alone accounts for
2.13M of the points the brief's "955,262 reclaimed" figure appears to assume.
That figure is not reproducible from the live host and I did not use it.

**Also refused (structural, no twin at all, points retained):**
`public_crypto_patterns` (2), `rug_events` (1,877), `scanned_tokens` (1).

## Hard guards

- Refuses to touch **4096** — the canonical lane. `RETIRABLE_DIMS = (1024, 2560)`.
- Refuses any width not in `(1024, 2560)`, including unparseable (`UNKNOWN`).
- Refuses a base whose twin is absent.
- Refuses a declared empty shell that now holds points.
- `--execute` requires `--yes`; exit 2 means Qdrant unreachable — `UNKNOWN`,
  never reported as clean.
- A Qdrant failure mid-run skips that collection; it is never marked deleted.

## Why the 1024 set exists

The 6 collections at 1024 (`exchange_flows`, `hyperliquid`,
`liquidation_events`, `token_unlocks`, `wallet_clusters`, `youtube_videos`) have
no `_8b` twin by design — they are pinned below canonical. All 6 hold 0 points,
so they are in Group B as empty shells, not as twin assertions.

## Verification

- 32 tests: `python3 -m pytest tests/test_drop_retired_bases.py -q`
- 10 mutants applied to the safety logic; 9 killed, 1 proven **equivalent**
  (removing the explicit 4096 check changed nothing because `RETIRABLE_DIMS`
  already excludes it — defence in depth, not a hole).
- One mutant initially survived and exposed a real gap: the plan-time
  allow-branch sat behind an unreachable `if`, so no test exercised it. The
  branch was collapsed into a single comparison and covered directly.
