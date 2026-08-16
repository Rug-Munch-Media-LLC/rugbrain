# Data Placement — rmi-codebase-mcp vs rmi-rag (canonical)

> The single decision point for "where does this data go?" when ingesting any
> new crypto source. Read this before writing an ingest script, adding a
> catalog entry, or wiring a new collection. Prevents duplicate storage and
> cross-boundary confusion between the two systems.

Status: **canonical** · owner=Rug Munch Media LLC Engineering · supersedes ad-hoc placement

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

**Never store the same snippet in both systems.** If it's code, it lives in
MCP; RAG reaches it via `mcp_bridge.py` (`search_codebase`,
`augment_code_query`) when a query is code-shaped. If it's facts, it lives in
RAG; MCP does not duplicate it.

---

## Consumers

| Layer | Consumer | Retrieval surface |
|-------|----------|-------------------|
| **MCP** | AI agents (the fleet's own toolset) + RAG bridge | `resolve_library`, `query_codebase`, `query_security_context` |
| **RAG** | End users (bot `/research` `/investigate`, frontend chat, scanner `Known Scam Patterns`) | `/api/v1/rag/v2/*`, `/api/v1/search` |

The bridge is the seam: RAG augments a code question by calling MCP
`query_codebase` + `query_security_context`; MCP never calls RAG.
