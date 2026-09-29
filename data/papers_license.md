# Papers / Standards / EIP sources — licence ledger

**Lane 3, 2026-09-29. Every row below was verified by a live fetch on that
date.** A prior researcher in this project reported 13 of 32 datasets alive
when they were dead, and a 14th dead when it was alive, purely because it
had no working search. Nothing here is asserted from a name — each row
records the exact command class that produced it.

## Operating ruling

This corpus is **INTERNAL and never shipped**. The operative licence test is
therefore **redistribution, not use**: CC-BY-NC, NonCommercial and
registration-gated sources ARE usable internally. This is why CC-BY-NC-ND
OpenSanctions is already ingested (`ingest_sources/licenses.py`).

The reason the ledger still exists: the day one of these corpora feeds a
shipped product, we must know **which rows were never cleared for that**.
Two classes are flagged separately for that reason:

- **NO LICENCE** — no rights statement at all. Internal research is
  arguable; redistribution is not. Never clear these for a shipped product.
- **COPYLEFT (GPL family)** — viral terms can reach derived artefacts.
  None of the rows harvested by this module are GPL, but one surveyed
  HuggingFace dataset is AFL-3.0, which is copyleft-adjacent.

## Harvested by `ingest_sources/papers.py` → collection `papers_standards`

| Source | URL | Licence | Records | Last modified | Usable internally |
|---|---|---|---|---|---|
| ethereum/EIPs | `https://github.com/ethereum/EIPs` | **CC0-1.0** (SPDX, confirmed via `/repos/.../license` → `LICENSE.md`, "CC0-1.0 Creative Commons Zero v1.0 Universal") | **957** files matching `EIPS/eip-<n>.md`; of those **592 substantive** + **365 moved-stubs** | `pushed_at` 2026-09-28 | **YES — unconditionally.** Public-domain dedication, no rights reserved. Redistribution OK. |
| DeFiLlama `/protocols` | `https://api.llama.fi/protocols` | **NO LICENCE DECLARED** — API carries no SPDX or terms header | **8,411** rows (100% carry `category`; 4,302 carry `address`) | Endpoint carries no last-modified field | **YES for internal research** (factual data, no copyright in the facts). **NO for redistribution** — see flag below. |

### Flag 1 — NO LICENCE: DeFiLlama `/protocols`

Verified: the response is a bare JSON array with no licence, terms link or
attribution header. A protocol *address* and *category* are facts, and facts
are not copyrightable, so storing them internally for research is low-risk.
But the API grants no redistribution right, and DeFiLlama publishes under
its own terms for the site as a whole. **Treat the harvested address
primitive as internal-only.** If it ever needs to ship, resolve terms first.

### Note — 365 of 957 EIPs are moved-stubs, not specifications

Verified by fetching all 957: 365 contain the literal
`This file was moved to https://github.com/ethereum/ercs/...` and average
under 400 bytes. The substantive ERC specs live in `ethereum/ercs`, which is
a **separate repo that returned HTTP 404** when probed and is therefore NOT
harvested. `papers.py` counts and reports these rather than silently padding
the corpus with 365 empty records.

## Surveyed, ALIVE, licence-clean or licence-known — NOT yet harvested

| Source | URL | Licence | Records | Last modified | Usable internally |
|---|---|---|---|---|---|
| bitcoin/bips | `https://github.com/bitcoin/bips` | **NO LICENCE** (`"license": null`) | repo 22,757 KB; per-file count not measured | 2026-09-28 | **YES** for internal research. Flag: no redistribution right. |
| solana-foundation/solana-improvement-documents | `https://github.com/solana-foundation/solana-improvement-documents` | **NO LICENCE** (`"license": null`) | repo 828 KB; per-file count not measured | 2026-09-25 | **YES** for internal research. Flag: no redistribution right. |
| gfissore/arxiv-abstracts-2021 (HF) | `https://huggingface.co/datasets/gfissore/arxiv-abstracts-2021` | **CC0-1.0** (`license:cc0-1.0` tag) | not measured (row count requires dataset download) | not measured | **YES — unconditionally.** The cleanest route to real abstracts. Requires the HF token for reliability. |
| ong/awesome-decentralized-finance | `https://github.com/ong/awesome-decentralized-finance` | **NO LICENCE** | list-repo, not document corpus | 2026-01-14 | Marginal — it is a link list, which is the same failure mode as the current RSS corpus. |
| dannberg/crypto-whitepapers | `https://github.com/dannberg/crypto-whitepapers` | **NO LICENCE** | **4** `.md` files (Bitcoin, Ethereum, Solana whitepapers) | **2021-12-21 (stale)** | **YES** internally, but it is a 4-file seed, not a corpus. |

## Flag 2 — COPYLEFT: `CShorten/ML-ArXiv-Papers` (AFL-3.0)

`https://huggingface.co/datasets/CShorten/ML-ArXiv-Papers` — licence tag
`license:afl-3.0`. Academic Free License 3.0 is a BSD-style permissive
licence with an attribution requirement; it is **not** GPL, but it is the
one copyleft-adjacent licence in the survey. Usable internally with
attribution. **Not harvested** — `gfissore/arxiv-abstracts-2021` is CC0 and
strictly better.

`ccdv/arxiv-summarization` (HF) also verified alive (7,934 downloads) but
carries **no licence tag at all** and its paper bodies are derived from
arXiv under mixed terms. **Excluded** in favour of the CC0 alternative.

## DEAD — verified, do not retry

| Source | Evidence |
|---|---|
| **KiwisLabs/crypto-whitepapers** | **All 3 probes HTTP 404**: `api.github.com/repos/…`, `raw.githubusercontent.com/…/README.md`, `github.com/…`. The `KiwisLabs` org itself is 404 — repo gone, not renamed. **The existing `ingest_sources/whitepapers.py` targets this dead source and can never yield a document.** Not edited by this lane (outside its scope). |
| ethereum/RFCs | `api.github.com/repos/ethereum/RFCs` → 404 |
| Consensys/solidity-smart-contracts | `api.github.com/repos/Consensys/solidity-smart-contracts` → 404 |
| ethereum/ERC-ABI | `api.github.com/repos/ethereum/ERC-ABI` → 404 |
| paperswithcode/paperswithcode (HF) | HF API → **HTTP 404** with the token. Note: an HF 401 means *unauthenticated*, not absent — this one is a true 404. |
| Microsoft/orca-math-word-problems-200k (HF) | **ALIVE** (MIT, verified) but off-topic — math word problems, not crypto papers. Listed to show it was checked, not skipped. |
| lucadiliello/triviaqa (HF) | **ALIVE**, no licence tag. Off-topic. |

## What I could NOT verify

- **`standards` collection is empty (0 points) and its owning lane is not
  me.** Verified via Qdrant `:6334` GET: exists, 4096-dim, 0 points. I did
  not create, populate or re-dim it.
- **DeFiLlama last-modified**: the `/protocols` endpoint exposes no
  last-modified field, so that cell is genuinely unknown, not estimated.
- **arXiv dataset row counts**: not measured — determining them requires a
  multi-hundred-MB download, which is out of budget here. The licence
  facts are verified; the counts are not.
- **Per-file counts for bitcoin/bips and solana-improvement-documents**:
  repo size is verified, document count is not.
- **HuggingFace 401 trap**: confirmed real. `gopass show -o
  rmi/huggingface/huggingface_token` (37 chars) is required; without it every
  dataset looks absent. Every HF row above was checked *with* the token.
