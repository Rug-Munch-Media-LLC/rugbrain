# RAG ↔ MCP Boundary — Design Specification

> **Status:** accepted v1 · owner=Rug Munch Media LLC Engineering
> **Supersedes:** nothing — extends `DATA-PLACEMENT.md` (the one-line rule survives)
> **Measured:** 2026-09-20 against live Qdrant + both service surfaces

Two systems, one substrate. This document specifies what each system is for,
what it needs, where they overlap (measured, not assumed), and the interface
options — so the merge/duplication question is decided on facts.

---

## 1. The substrate they already share

| Layer | Reality (2026-09-20, verified against code + live Qdrant) |
|---|---|
| Vector store | **ONE** Qdrant instance (`rmi-qdrant-v2`; container :6333, host :6334). 41 collections. |
| Embedder | **ONE** stack, both systems identical: **`Qwen/Qwen3-Embedding-8B` (2560d)** — DeepInfra (canonical primary) + local llama.cpp `:8004` (same model ⇒ index-compatible fallback). Locked doctrine 2026-08-29. |
| Vector space | **Identical** — every collection measured at 2560d (MCP *and* RAG), incl. named sparse vectors on `codebase_reference_v2` + `rag_documents`. |
| Reranker | **`Qwen/Qwen3-Reranker-8B`** — local llama.cpp `:8003` + DeepInfra hosted (`/v1/inference/Qwen/Qwen3-Reranker-8B`), same family both lanes. 0.6B tier retired 2026-09-14. |
| Legacy lanes | gateway `:8010` / OpenRouter / NVIDIA NIM — explicit `RAG_EMBED_BACKEND` overrides only, not in the default path. |

> **Doc-drift warning (fixed 2026-09-20):** `DATA-STRATEGY.md` described an
> "Ollama BGE-M3" stack and a *deferred* reranker — both obsolete. The truth
> is the table above (code is canonical: `src/rugbrain/embeddings.py`,
> `rerank_hosted.py`). `rmi-backend/app/rag/QDRANT-CONSOLIDATION.md` also
> said 1024d — corrected to 2560d. When docs and code disagree here, the
> code + a live `/collections/<name>` dimension check win.

**Consequence:** the split between the systems is **collection + consumer
surface**, not infrastructure. A cross-system retrieval is technically trivial
(same space, same instance); the real questions are governance and ownership.

---

## 2. System specifications

### 2.1 ContextCrypto (MCP) — the *code reference + agent operations* layer

- **Purpose:** let agents *look at code and operate*: source snippets, repo
  structure, call graphs, security patterns-as-code, our own repos + canon,
  plus the fleet routing layer (agent/skill/model lanes) and content guards.
- **Consumers:** opencode agents (via the LiteLLM MCP gateway, 209 tools),
  lead orchestrators. No product traffic.
- **Data (2.06M chunks):** 200 third-party repos + 15 `internal/*` repos
  (+`internal/standards` canon, `internal-lessons/*`), exploit **code**
  (DeFiHackLabs PoCs), `security_patterns` (1,956), `master_skills` (4,334),
  `protocol_design` (58), `cross_chain` (13), third-party `docs:*` ingests.
- **Surfaces:** 52 MCP tools; facets `platform / good_code / bad_code /
  our_ways`; guards (injection/secrets/danger index); repo maps (205 libs).
- **Owns:** the code corpus, the routing layer, dispatch telemetry.
- **Needs (open):** graph-health alerting (historical 33–50% tool failure),
  provenance (10/13 watched repos CI-failing), eval harness, remaining repo
  coverage decisions (rugcharts, rugmaps, x402-gateway, fleet-template,
  searxng-mcp).

### 2.2 RugBrain (RAG) — the *knowledge / entity / incident* layer

- **Purpose:** answer questions with facts: scam indicators, incidents,
  news/social, wallet/entity intelligence, audits-as-text, regulation.
- **Consumers:** **product surfaces only** — rmi-backend `app/rag/*` (HTTP),
  Telegram bot (`/research`, `/investigate`), frontend AI chat. Agents have
  **no** RugBrain surface today.
- **Deployment:** `rugbrain-api.service` (systemd, 127.0.0.1:8012; prod
  `rugbrain.rugmunch.io/api/v1`), nightly B2 permanence snapshots.
- **Data (~447k points, 22 populated collections):** `news_v2` 143,985 ·
  `scam_intel_v2` 99,400 · `rag_documents` 46,235 · `wallet_profiles` 40,355 ·
  `known_scams` 26,720 · `social_intel` 20,979 · `threat_intel_v2` 16,741 ·
  `defi_exploits` 16,169 · `wallet_v2` 9,657 · `scam_patterns` 7,091 ·
  `regulation_enforcement` 5,068 · `crypto_vulnerabilities` 4,892 ·
  `live_threats` 2,792 · `education` 2,080 · `contract_security` 1,079 ·
  `contract_audits` 902 · `security_feeds` 741 · `market_health` 627 ·
  `stablecoin_market` 336 · `research_v2` 151 · `github_audits` 146 ·
  `security_alerts` 86 · `hacks_v2` 8 · (+8 empty shells).
- **Surfaces:** `/search`, `/rag/v2/scam-check` (3-pillar), `/rag/query`,
  `/federated` (multi-collection RRF + provenance), `/rag/investigate`,
  `/scanner/deployer-risk`, `/rag/chain/query`.
- **Owns:** all knowledge collections, the ingestion crons, retention.
- **Needs (open, ranked):**
  1. **Finish the `rag_documents` migration** (`doc_id = source:content_type:sha256(text)[:16]`,
     per `rmi-backend/app/rag/QDRANT-CONSOLIDATION.md`) — content-addressed IDs
     make duplication structurally impossible (identical text = same point ID = upsert).
  2. **Noise**: `news_v2` (143,985) is raw Telegram firehose — measured sources
     include non-crypto channels (`t.me/IranintlTV`, `t.me/akharinkhabar`,
     `t.me/u_now`). Needs a topical filter or demotion before any agent exposure.
  3. **Merge the scam cluster** (3 collections, overlapping sources) and the
     **wallet cluster** (2 collections, same source).
  4. **Registry freshness**: `docs/COLLECTION_REGISTRY.md` is ~1 month stale
     (news_v2 listed 4,124 vs 143,985 live; scam_intel_v2 600 vs 99,400).
  5. Retention/TTL per source; eval (`docs/EVALS.md` exists).
  6. **Documentation debt** (caused real confusion, 2026-09-20): registry
     stale by ~1 month; `DATA-STRATEGY.md` described a retired embed stack;
     `QDRANT-CONSOLIDATION.md` had wrong dims. Two fixed same-day; automate
     registry generation from live Qdrant so drift is impossible.

---

## 3. Measured duplication (the operator's concern, verified)

**Verdict: the MCP ↔ RAG boundary is CLEAN. The duplication is RAG-internal.**

| Source | Appears in | Assessment |
|---|---|---|
| `defihacklabs` | `exploits` (MCP, **PoC code**), `defi_exploits` (RAG writeups), `threat_intel_v2` (RAG writeups) | Correct split code↔text; **RAG side duplicated twice** → one canonical writeup collection |
| `etherscan_combined` | `known_scams`, `wallet_profiles`, `wallet_v2` | Same source in 3 collections → consolidate wallet/entity |
| `phishdestroy_destroylist` | `known_scams`, `scam_intel_v2` | Scam indicator duplication |
| `cryptoscamdb` / `metamask_phishing` / `scamsniffer` / `openphish` / `blocksecteam` | spread over `known_scams`, `scam_patterns`, `scam_intel_v2` | 3 collections for one domain → merge |
| telegram/social | `news_v2`, `social_intel`, `rag_documents` | 3 copies; migrate → `rag_documents`, retire legacy |
| `trailofbits` | `contract_audits` (findings), `research_v2` (blog) | Complementary; keep, label by `content_type` |
| `cisa_kev` / `ossf_malicious_packages` / `swc_registry` | `crypto_vulnerabilities` only | Unique ✓ |
| `live_threats` (clickhouse dex/oracle) | unique | Unique ✓ |

**Historical precedent:** `rmi-rag` vs `rugbrain` — 274 byte-identical files
(~80K lines). `rmi-rag` is formally deprecated (2026-09-05). The fleet has
already paid for duplication once; the fix that worked was *one canonical
repo + explicit deprecation rules* — apply the same pattern to collections.

---

## 4. Boundary rules (extends `DATA-PLACEMENT.md`)

The one-line rule survives: **Code → MCP. Knowledge → RAG.** Refinements:

1. **Content-type test** for any source: *code payload* (`.sol`, `.py`, PoC
   source, patterns-as-code, skills, our canon/docs) → **MCP**; *fact payload*
   (writeups, incidents, news, social, indicators, entities, audits-as-text,
   regulation) → **RAG**.
2. **One datum, one home.** A source may feed both systems only if the
   *content type differs* (DeFiHackLabs `.sol` → MCP; its README → RAG).
   Never the same text in two collections.
3. **Content-addressed IDs everywhere** (`sha256(source:content_type:text)`):
   re-ingest becomes idempotent; duplication becomes detectable (identical
   hash) instead of invisible.
4. **Provenance is mandatory** in payloads: `source`, `content_type`,
   `collection` (legacy provenance), `updated_at` — already the RAG target
   schema; adopt it in MCP payloads where missing.
5. **Neither system holds live data.** Market/health snapshots (TVL, prices,
   liquidations) are API reads; only *historical* snapshots belong in RAG.
6. **Ownership is explicit**: MCP collections → ContextCrypto; knowledge
   collections → RugBrain; `rgm_*` (wallet graph) → rmi-backend (already
   documented in the registry).

---

## 5. Interface options

### Option A — Hard split (status quo + cleanup)
Agents: MCP only. Products: RugBrain API only. No cross-calls.
**Pros:** zero coupling; simplest. **Cons:** agents cannot reach facts
(scam/wallet/news) when a task needs them (e.g. "is this deployer known?");
products cannot reach code.

### Option B — Read-through bridge **(recommended)**
ContextCrypto MCP gains a **small, read-only** set of knowledge tools that
proxy RugBrain's HTTP API on localhost:

```
query_rugbrain(query, domains=["threat","news","scam","wallet"], limit)
threat_intel(address)        -> /rag/v2/scam-check
wallet_profile(address)      -> /rag/investigate (read-only fields)
```

- **No data duplication** — RugBrain remains the single owner of its
  collections; MCP never writes to them; nothing is re-embedded.
- Provenance is explicit in every result (`source: rugbrain/<collection>`).
- Scope: 2–3 tools, one new facet (`intel`), deny-by-default for write paths.
- RugBrain's `/federated` (RRF + backend provenance) already does the hard part.

**Phasing:** (1) fix RAG-internal duplication + noise first (it is the actual
problem); (2) add the read-through tools; (3) measure agent usage.

### Option C — Unified retrieval facade
One router over both corpora (same vector space makes it possible).
**Pros:** maximal recall. **Cons:** creates a third system to govern;
duplicates the facet/routing logic that already exists on both sides; makes
provenance fuzzier. **Not recommended now** — revisit only if agent usage
proves Option B insufficient.

---

## 6. Work items (owner = the system that owns the data)

**RugBrain:** finish `rag_documents` migration → retire `news_v2`/`social_intel`
legacy; merge scam cluster → one collection; consolidate wallet cluster;
topical filter for Telegram; per-source retention; refresh
`COLLECTION_REGISTRY.md` from live Qdrant (automate); expose the small
read-only surface Option B needs (already exists: `/search`, `/rag/v2/scam-check`,
`/rag/investigate`).

**ContextCrypto:** graph-health alerting; provenance (CI); optional new repos;
`intel` facet + read-through tools (only if Option B is chosen); keep the
ingest-side boundary enforced (the `internal/*` prose exception is the only
intentional deviation).

**Both:** one shared eval set for "did the agent/product get the right
evidence" — the boundary is only proven by evals.

---

## 7. Accepted audience boundary (v1)

The two systems share infrastructure, not authority. The implemented policy
is fail-closed:

| Audience | May retrieve | Must not retrieve |
|---|---|---|
| **RugBrain public** | Explicitly allowlisted crypto-intelligence collections plus reviewed public/open-source crypto code and security-pattern projections (`defi_exploits`, `contract_audits`, `crypto_vulnerabilities`, `threat_intel_v2`, `public_crypto_patterns`, and curated product knowledge) | ContextCrypto internal/private code collections, our own app/repo source coordinates, prompts, model/provider identity, routing/configuration, fleet paths, secrets, or raw tool payloads |
| **ContextCrypto internal** | Full code graph, `codebase_reference`, `security_patterns`, internal repos, skills, routing, and explicitly public projections | Nothing is silently copied into the public audience; internal retrieval is read-through or operator-scoped |

Implementation rules:

1. `knowledge_boundary.py` is the single collection allowlist. New collections
   are private until reviewed and added explicitly.
2. Public search and ingestion reject ContextCrypto/internal collection names
   with a generic error that does not confirm the collection exists.
3. Public responses pass through a recursive sanitizer that removes source
   paths, prompts, model/provider fields, raw payloads, and graph coordinates.
4. Raw ContextCrypto code retrieval remains internal-only. The public
   `/search/crypto-code` surface reads only reviewed public/open-source
   projections; it never falls back to `codebase_reference`,
   `security_patterns`, or our own repositories.
5. Public code projections may contain genuinely useful code snippets from
   reviewed public/open-source crypto libraries, with public URLs and license
   metadata. Internal source paths, private repos, prompts, secrets, and
   unreviewed PoCs never enter `public_crypto_patterns`.
6. The public product may explain zero-days and exploit mechanics at a
   defensive/educational level and provide authorized regression tests. It
   refuses requests to deploy, drain, steal, evade, or otherwise operationalize
   an attack against someone else's systems.
7. The public product may state **what** a vulnerability or exploit does and
   cite public evidence. It must not reveal retrieval infrastructure, model
   identity, system prompts, internal source paths, or security-detection
   methodology. Internal agents use ContextCrypto for those questions.

The next migration step is a redacted, provenance-preserving projection from
approved ContextCrypto patterns into `public_crypto_patterns`; it is not a
bulk copy and does not re-embed the same internal payload into a public path.

The projection contract is implemented in
`src/rugbrain/public_projection.py`. An operator runs
`python scripts/ingest_public_contextcrypto.py reviewed.jsonl` for a dry run,
then uses `--apply` only after the source, license, and public-safety review.
The importer reads no ContextCrypto collection directly; it accepts only
records already marked `audience=public` and an approved public source kind;
code snippets additionally require `source_kind=contextcrypto_public_code` and
`public_safe=true`. The reviewed two-record seed is
`data/public_crypto_patterns_seed.jsonl`; expand it only through the same
projection and leak-scan gates.

---

## 8. Decision checklist

1. **Interface:** A (hard split) or **B (read-through bridge, recommended)**?
2. **RAG consolidation:** execute the `QDRANT-CONSOLIDATION.md` plan now
   (unified `rag_documents` + retire legacy), or after the bridge?
3. **Telegram firehose:** filter topically (keep ~?%) or keep raw + demote?
4. **Retention:** per-source TTLs — news 30d? indicators permanent? decide.
5. **Registry:** automate `COLLECTION_REGISTRY.md` generation from live Qdrant?
