# Data Placement — rmi-codebase-mcp vs rmi-rag (canonical)

> The single decision point for "where does this data go?" when ingesting any
> new crypto source. Read this before writing an ingest script, adding a
> catalog entry, or wiring a new collection. Prevents duplicate storage and
> cross-boundary confusion between the two systems.

Status: **canonical** · owner=Rug Munch Media LLC Engineering · supersedes ad-hoc placement

---

## Permanence vs backup — the binding policy (2026-10-15)

> **Permanence lives on the server. B2 is backup only.**
> **Blockchain data is the exception: it is authoritative on B2.**

This is the operator's binding policy. It supersedes every earlier framing,
including the rejected "B2 permanence" idea for a context7-like code MCP.

| Tier | What lives there | Role | Permanence |
|------|------------------|------|------------|
| **Server (local, hot)** | The code/RAG corpus — code, docs, RAG collections, security patterns, skills. Qdrant + Redis + Neo4j on the box. | **Serving path.** The hot working set an agent queries. | **Authoritative.** Kept local so it can be structurally organized and served fast. |
| **B2 (cold / DR)** | Parquet snapshots of the Qdrant collections + Qdrant snapshots, written nightly by `scripts/b2_permanence.py`. | **Backup only.** Disaster recovery and cold retention. | **Backup.** Never a serving path for the code/RAG corpus. |
| **B2 + parquet + DuckDB + B2 MCP** | **Blockchain data** — chain-indexer parquet (transfers, DEX swaps, traces), the 90-day BigLake backup. | **Authoritative store** for chain data we do not index ourselves. | **Authoritative.** This is the one place B2 *is* the permanence tier. |
| **DuckDB / parquet** | The read path over the B2-hosted blockchain data. | Derived query layer. | Derived. |

**The rule in one sentence:** the code/RAG corpus is served from the server and
backed up to B2; blockchain data is served from B2/parquet/DuckDB and is
authoritative there.

**Naming caveat — `permanence` in this repo means "the B2 backup job".**
`src/rugbrain/permanence.py`, `scripts/b2_permanence.py`, the
`rugbrain-permanence.{service,timer}` unit, and the `rmi-rag-permanence` bucket
are all the **nightly Qdrant→B2 backup**, not a claim that B2 is the serving
tier. The name is historical; read it as "backup". Do not rename the service or
the bucket in this lane — the name is load-bearing in systemd, the
EnvironmentFile, and the bucket itself.

**Rejected framing (do not reintroduce):** "B2 permanence" as the storage model
for a context7-like code MCP. A code server needs its hot set local; B2 is the
cold/bulk tier. Do **not** add a B2 offload path for `codebase_reference_v2_8b`
or any code/RAG collection.

---

## The one-line rule

> **Code → MCP. Knowledge → RAG.**

- **rmi-codebase-mcp** is the *code reference*: source-code snippets
  (contracts / functions / modules), AST-chunked, verdict-tagged.
- **rmi-rag** (RugBrain) is the *knowledge + incidents + entities* layer:
  natural-language writeups, post-mortems, blacklists, phishing, news/social,
  wallet/entity graphs, audit findings-as-text.

If a new source is ambiguous, answer this single question: **"is the payload
something an agent would retrieve to *look at code* (MCP), or to *answer a
question with facts* (RAG)?"**

Audience is a second, non-negotiable gate:

- **ContextCrypto/internal:** code, source paths, graph metadata, prompts,
  model/provider/routing details, internal methodology, and security
  architecture.
- **RugBrain/public:** curated crypto facts, incidents, audit findings, public
  vulnerability explanations, and explicitly reviewed public/open-source code
  or security-pattern projections. Internal code and model identity never
  become public RAG data.
- A source can cross the boundary only through a redacted, allowlisted
  projection with provenance and license metadata. Never expose a raw
  ContextCrypto collection through a public search or ingestion route.

---

## Placement table

| Data type | Destination | Collection / catalog | Verdict |
|-----------|-------------|----------------------|---------|
| Audited protocol/library source (OZ, Uniswap, Aave, Compound, Solady…) | **MCP** | `codebase_catalog.json` → `codebase_reference` | `clean` |
| Exploit PoC source (DeFiHackLabs `.sol`, honeypots, rug-pull templates) | **MCP** | `vulnerable_catalog.json` → `codebase_reference` | `malicious` |
| Known-buggy reference code / CTF challenge code | **MCP** | `vulnerable_catalog.json` → `codebase_reference` | `vulnerable` |
| Vulnerability/defense **patterns as code** (SWC, reentrancy guard, oracle-manip) | **MCP** | `security_patterns` (Qdrant) | `weakness`/`defense` |
| Incident **post-mortems / writeups** (Rekt, SlowMist KB, samczsun, hack analyses) | **RAG** | `defi_exploits` / `hacks_v2` / `threat_intel_v2` | — |
| Structured hack DB (DeFiLlama hacks API, incident databases) | **RAG** | `threat_intel_v2` / `defi_exploits` | — |
| Blacklists / phishing (CryptoScamDB, scamsniffer, drainer domains) | **RAG** | `known_scams` / `scam_intel_v2` / `crime_reports` | — |
| Audit **findings-as-text** (Code4rena, Sherlock, OZ audits, solodit) | **RAG** | `contract_audits` / `audit_findings` | — |
| News / social / market intel | **RAG** | `news_v2` / `social_intel` / `research_v2` | — |
| Wallet labels / entity graphs / clusters | **RAG** | `wallet_v2` — `rgm_*` is owned by **rmi-backend (Neo4j)**, not RAG | — |
| Regulation / enforcement (SEC/DOJ/CFTC) | **RAG** | `regulation_enforcement` | — |
| Vulnerability **registries as text** (SWC registry prose, SCVS checklist) | **RAG** | `crypto_vulnerabilities` | — |

---

## Overlap tiebreakers (a single repo spans both)

When one upstream repo contains BOTH code and prose, **split by file type,
never by repo**:

1. **`.sol` / `.rs` / `.vy` / `.move` source** → **MCP** (AST-chunked).
   The README/prose in the same repo → **RAG** if it carries incident facts.
2. **DeFiHackLabs**: `.sol` PoCs → MCP `codebase_reference` (`malicious`);
   incident metadata (chain, $ lost, technique) → RAG `defi_exploits`.
3. **SWC Registry**: weakness *definitions + code samples* → MCP
   `security_patterns`; classification prose → RAG `crypto_vulnerabilities`.
4. **Audit reports**: findings prose → RAG `contract_audits`; the audited
   contract code itself (if ingested) → MCP `codebase_reference` (`clean`).

**Never store the same snippet in both systems.** If it's internal code, it
lives in ContextCrypto and is not copied into RugBrain. A reviewed
public/open-source exception may create a redacted derivative in
`public_crypto_patterns` with a public URL and license; the public bot reads
that projection, while internal agents continue to use the MCP/code graph.
Public RAG does not call the internal bridge for code retrieval.

---

## Consumers

| Layer | Consumer | Retrieval surface |
|-------|----------|-------------------|
| **MCP** | AI agents (the fleet's own toolset) + RAG bridge | `resolve_library`, `query_codebase`, `query_security_context` |
| **RAG** | End users (bot `/research` `/investigate`, frontend chat, scanner `Known Scam Patterns`) | `/api/v1/rag/v2/*`, `/api/v1/search`, public `/api/v1/search/crypto-code` projection |

The bridge is an internal seam only: RugBrain's public code route never calls
MCP; it reads only `public_crypto_patterns`. MCP never calls RAG.
