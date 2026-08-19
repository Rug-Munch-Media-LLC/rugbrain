# GraphRAG — Hybrid Vector + Knowledge-Graph Retrieval for Scam-Wallet Networks

**Status:** design contract (v1) · **Owner:** crmuncher · **Date:** 2026-08-15 · **Repo:** `RugMunchMedia/rugbrain`
**Scope:** pure design/planning. This document specifies the GraphRAG architecture for
scam-wallet network retrieval. No production code was written to produce it; implementation
happens in later phases and is subject to the acceptance gates in §7.

> **How to use this document**
> - Sections 1–6 are normative. Implementing agents MUST NOT deviate without a written note in the PR.
> - All node labels, edge types, and property keys are exact and MUST be used verbatim.
> - Where a node/edge already exists in rmi-backend's Neo4j schema, it is marked **[EXIST]** and
>   MUST be reused, never reinvented. New labels/edges are marked **[NEW]**.
> - Facts marked `[verified]` were confirmed against the live repos on 2026-08-15.

---

## 0. Non-Negotiables (inherited from ARCHITECTURE-V2.md, binding)

1. **ONE graph store:** the existing rmi-backend Neo4j instance (`rmi-neo4j`, Neo4j 5.x community,
   `bolt://rmi-neo4j:7687`). No second graph database, no Redis-as-graph, no new service. The
   Redis-backed `graph_rag.py` community code in rugbrain (`src/rugbrain/graph_rag.py`) was the
   **ancestor** of this design's Phase 2 — it is re-implemented over Neo4j, not extended.
   **Current status (2026-08-16):** `graph_rag.py` and the `GET /api/v1/rag/graph-communities`
   route are **deleted** (`aadf85f`); the `synthesize_answer` shim it exposed lives on in
   `src/rugbrain/ratings.py` (kept, shared rating node), and its graph tests were renamed
   (`test_synthesize_answer_node`). No code in rugbrain imports the deleted module.
2. **ONE vector store:** Qdrant (`rmi-qdrant:6333`), dense 1024-d BGE-M3 + BM25 sparse. GraphRAG
   adds a **graph lane** to retrieval; it does not add a second vector store.
3. **Graph read path lives in rmi-backend** (it already owns the Neo4j driver, entity resolution,
   and `app/domains/wallet/neo4j_client.py`). rugbrain never talks to Neo4j; it receives graph
   context as *documents* in the search request/response contract.
4. **Fail-open for the graph lane, fail-closed for the vector lane.** If Neo4j is down: the graph
   lane returns zero graph evidence and the search still succeeds on vectors. If Qdrant/Infinity is
   down: 503 per existing contract. Rationale: graph evidence is additive enrichment; vectors are
   the primary grounding.
5. **Latency budget:** the bot chat path has a ~5 s RAG budget (`RAG_SEARCH_TIMEOUT = 5.0` in
   `app/routers/chat/_rag.py`). GraphRAG must stay inside it — see §5.

---

## 1. Node + Edge Schema (scam-wallet graph)

All labels/keys below are exact. Existing schema is confirmed from
`app/domains/wallet/neo4j_client.py`, `app/cross_chain_tracking/neo4j_writer.py`, and
`app/catalog/service.py` in rmi-backend `[verified]`.

### 1.1 Nodes

| Label | Status | Identity property (unique constraint) | Key properties |
|---|---|---|---|
| `Wallet` | **[EXIST]** | `wallet_id` (`"{chain}:{address_lower}"`) | `address`, `chain`, `is_deployer`, `chains_active`, `tx_count`, `total_volume_usd`, `first_seen`, `last_seen` |
| `Entity` | **[EXIST]** | `entity_id` (`"ent_" + uuid4hex[:16]`) | `label`, `aliases[]`, `tags[]`, `first_seen`, `last_seen` |
| `Label` | **[EXIST]** | none (natural key `(name, category, source)`) | `name`, `category`, `source` — wallet labels from ClickHouse `wallet_memory.wallet_labels` |
| `Contract` | **[NEW]** | `contract_id` (`"{chain}:{address_lower}"`) | `address`, `chain`, `name`, `bytecode_hash`, `verified`, `deployer_wallet_id`, `first_seen`, `last_seen` |
| `TransactionHash` | **[NEW]** | `tx_hash` (lowercase, 0x-prefixed) | `chain`, `block_number`, `timestamp`, `from_wallet_id`, `to_address`, `value_eth`, `method_signature`, `status` |
| `VulnerabilityClass` | **[NEW]** | `vuln_id` (`"SWC-101"`, `"ruckus-*"` for ApeSec, etc.) | `name`, `category` (= `attack_vector` taxonomy from DATA-STRATEGY §7.1: `reentrancy`, `oracle_manipulation`, …), `severity`, `description`, `source` |
| `Protocol` | **[NEW]** | `protocol_id` (`"{chain}:{slug}"`) | `name`, `chain`, `tvl_usd`, `category` (`lending`, `dex`, `bridge`, …), `url` |
| `DrainerKit` | **[NEW]** | `kit_id` (slug, e.g. `"inferno-drainer"`) | `name`, `first_seen`, `last_seen`, `active`, `distributor_wallet_id`, `source` |
| `BlacklistSource` | **[NEW]** | `source_id` (`"scamsniffer"`, `"cryptoscamdb"`, `"forta"`, `"apesec"`, …) | `name`, `url`, `last_updated`, `license` |
| `Domain` | **[NEW]** | `domain` (lowercase, no scheme) | `first_seen`, `last_seen`, `category` (`phishing`, `scam`) |

**Reuse rule:** `Wallet`, `Entity`, `Label`, and all wallet-to-wallet edges are ALREADY written by
rmi-backend (entity resolution, deployer matching, ClickHouse label sync). RugBrain's graph
ingestion adds the **[NEW]** nodes and the scam-specific edges below, and writes into the SAME
Neo4j instance. It never re-creates `Wallet`/`Entity`/`Label` — it MERGEs them.

### 1.2 Edges

| Edge type | Direction | Status | Properties | Meaning |
|---|---|---|---|---|
| `LABELED_AS` | `(Wallet)-[:LABELED_AS]->(Label)` | **[EXIST]** | `source`, `confidence`, `is_sanctioned` | wallet carries a known label (e.g. phishing) |
| `BELONGS_TO` | `(Wallet)-[:BELONGS_TO {confidence:1.0}]->(Entity)` | **[EXIST]** | `confidence` | wallet resolved to cross-chain entity |
| `SAME_AS` | `(Wallet)-[:SAME_AS]-(Wallet)` | **[EXIST]** | `confidence`, `method`, `created_at` | same entity across chains |
| `BRIDGED_TO` | `(Wallet)-[:BRIDGED_TO]-(Wallet)` | **[EXIST]** | `bridge_name`, `tx_hash`, `confidence`, `created_at` | funds bridged between wallets |
| `BEHAVIORAL_MATCH` | `(Wallet)-[:BEHAVIORAL_MATCH]-(Wallet)` | **[EXIST]** | `confidence`, `evidence`, `created_at` | fingerprint similarity |
| `FUNDED_BY_SAME` | `(Wallet)-[:FUNDED_BY_SAME]-(Wallet)` | **[EXIST]** (traversal only, see catalog `resolve_entity`) | `confidence`, `method` | funded by the same source |
| `CLONE_OF` | `(Wallet)-[:CLONE_OF]-(Wallet)` | **[EXIST]** (traversal only) | `confidence`, `method` | clone-deployer relationship |
| `EXECUTED` | `(Wallet)-[:EXECUTED]->(TransactionHash)` | **[NEW]** | `role` (`initiator` \| `payer`), `confidence` | attacker/funder wallet signed the tx |
| `EXPLOITED` | `(TransactionHash)-[:EXPLOITED]->(Contract)` | **[NEW]** | `amount_lost_usd`, `confidence` | tx exploited the contract |
| `DEPLOYED_BY` | `(Contract)-[:DEPLOYED_BY]->(Wallet)` | **[NEW]** | `confidence`, `method` (`create2` \| `create` \| `proxy`), `created_at` | contract deployer (mirrors `Wallet.is_deployer`) |
| `LABELED_AS` | `(Wallet)-[:LABELED_AS]->(VulnerabilityClass)` | **[NEW]** (reuses edge type name) | `source`, `confidence` | wallet is associated with a vulnerability class (e.g. a drainer-wallet behavioral class) |
| `FLAGGED_BY` | `(Contract)-[:FLAGGED_BY]->(BlacklistSource)` | **[NEW]** | `first_seen`, `last_seen`, `reason` | contract blacklisted by a source |
| `VULNERABLE_TO` | `(Contract)-[:VULNERABLE_TO]->(VulnerabilityClass)` | **[NEW]** | `confidence`, `source` (`swc` \| `defihacklabs` \| `slowmist` \| `audit`) | contract exhibits the class (from PoC/incident/audit) |
| `BELONGS_TO` | `(Contract)-[:BELONGS_TO]->(Protocol)` | **[NEW]** (reuses edge type name) | `confidence`, `role` (`implementation` \| `proxy` \| `token`) | contract is part of a protocol |
| `USES_KIT` | `(Contract)-[:USES_KIT]->(DrainerKit)` | **[NEW]** | `confidence`, `evidence` | contract deployed from a drainer kit |
| `USES_KIT` | `(Wallet)-[:USES_KIT]->(DrainerKit)` | **[NEW]** | `confidence`, `first_seen` | wallet interacts with/operates a drainer kit |
| `HOSTS` | `(Domain)-[:HOSTS]->(Wallet)` | **[NEW]** | `confidence`, `first_seen` | phishing domain points at scam wallet address |
| `HOSTS` | `(Domain)-[:HOSTS]->(Contract)` | **[NEW]** | `confidence`, `first_seen` | phishing domain points at scam contract address |

**Canonical multi-hop path (the core scam-network motif):**

```
(Wallet)-[:EXECUTED]->(TransactionHash)-[:EXPLOITED]->(Contract)-[:FLAGGED_BY]->(BlacklistSource)
                                    │
                                    └──(Contract)-[:VULNERABLE_TO]->(VulnerabilityClass)
                                    └──(Contract)-[:BELONGS_TO]->(Protocol)
                                    └──(Contract)-[:USES_KIT]->(DrainerKit)
```

### 1.3 Constraints & indexes (add to Neo4j, idempotent `IF NOT EXISTS`)

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Contract)          REQUIRE c.contract_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (t:TransactionHash)   REQUIRE t.tx_hash IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (v:VulnerabilityClass) REQUIRE v.vuln_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (p:Protocol)          REQUIRE p.protocol_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (k:DrainerKit)        REQUIRE k.kit_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (b:BlacklistSource)   REQUIRE b.source_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (d:Domain)            REQUIRE d.domain IS UNIQUE;
CREATE INDEX IF NOT EXISTS FOR (c:Contract) ON (c.address, c.chain);
CREATE INDEX IF NOT EXISTS FOR (t:TransactionHash) ON (t.timestamp);
```

---

## 2. Retrieval Flow — hybrid fusion in `/api/v1/rag/v2/search`

### 2.1 Where fusion happens

```
bot / frontend chat / scanner
   │  POST /api/v1/rag/v2/search            (rmi-backend, existing route + auth)
   ▼
rmi-backend search handler  ───►  vector lane: POST rugbrain-api:8012 /api/v1/search
   │                                  (Qdrant dense+BM25+RRF over bot's 5 collections)
   │
   ├──►  graph lane: rmi-backend Neo4j queries  (app/domains/wallet/neo4j_client.py
   │         + NEW app/domains/rag_graph/ readers — bounded multi-hop traversal, §5)
   │
   └──►  FUSION: RRF over (vector hits, graph-evidence docs) → response.hits
```

The response schema of `POST /api/v1/rag/v2/search` is extended backward-compatibly:
`SearchResponse.hits[]` gains an optional `graph` object per hit
(`{evidence_type, path, confidence, hop_depth, edge_types[]}`) and a top-level
`graph_summary` block. Bot/SDK consumers that ignore unknown fields keep working.

### 2.2 The two lanes

**Vector lane (unchanged mechanics):** rugbrain `search/engine.py` hybrid — dense BGE-M3 + BM25
sparse + RRF, optional rerank (self-hosted Qwen3-Reranker-8B, llama.cpp :8003), optional HyDE/multi-query, MMR dedup. The bot
fans out over its **5 knowledge collections** `[verified, 2026-08-15
`app/domains/telegram/rugmunchbot/tools/rag_lookup.py`]`:

| # | Collection | Qdrant content | Graph counterpart (§3) |
|---|---|---|---|
| 1 | `security_patterns` | curated vuln patterns (SWC registry, verdict/severity) | `VulnerabilityClass` nodes |
| 2 | `codebase_reference` | secure reference snippets, reputable libs | (context; rarely graphs) |
| 3 | `defi_exploits` | exploit writeups / PoCs (13.6 K pts) | `TransactionHash`→`EXPLOITED`→`Contract` edges |
| 4 | `contract_security` | verdict-labeled good/bad contract patterns | `Contract` + `VULNERABLE_TO` + `FLAGGED_BY` |
| 5 | `scam_patterns` | scam pattern docs + rug_timeline alias (9.9 K pts) | `DrainerKit`, `Label`(phishing), `Domain` |
| (token questions) | `scanner_v2` | scan-to-RAG memory (191 pts) | `Contract` + `Wallet` labels |

**Graph lane (new):** in rmi-backend, `app/domains/rag_graph/` (new module, no rugbrain import —
respects ARCHITECTURE-V2 §6 no-`app.rag` rule; it is a NEW domain module, not `app.rag`):

1. **Seed anchor extraction:** deterministic extraction of wallet/contract addresses
   (`0x[0-9a-fA-F]{40}`), domains, and SWC ids (`SWC-\d+`) from the query. Also reuses the
   vector lane's top-3 hit payloads (which carry `address`, `token_address`, `tx_hash`,
   `protocol`, `vuln_id` metadata) as additional anchors — a hit's metadata is a graph key.
2. **Bounded traversal:** per anchor, run the §5 query set (cap: 3 anchors × depth ≤ 3,
   `LIMIT 25` per anchor). Every result is materialized as a **graph-evidence document**
   (JSON payload described in §2.3) so fusion downstream treats graph and vector uniformly.
3. **Graph evidence → vector-space document:** no embedding of graph docs at query time
   (too slow). Instead the evidence doc carries `id = "graph:{wallet_id}:{path_hash}"` and
   ranking is by graph score (§2.4), NOT by cosine.

### 2.3 Graph-evidence document shape (internal fusion unit)

```json
{
  "id": "graph:ethereum:0xabc…:d305…",
  "collection": "__graph__",
  "source": "graph",
  "content": "Wallet 0xabc… EXECUTED tx 0xdef… EXPLOITED Contract 0x111… (FLAGGED_BY scamsniffer) …",
  "metadata": {
    "anchor": "0xabc…",
    "edge_types": ["EXECUTED", "EXPLOITED", "FLAGGED_BY"],
    "path": "Wallet→Tx→Contract→BlacklistSource",
    "hop_depth": 3,
    "graph_score": 0.87
  }
}
```

### 2.4 Fusion algorithm (RRF + graph boost)

```
ranks_vector  = RRF-ordered vector lane hits          (k=60, existing rrf_fusion)
ranks_graph   = graph evidence docs ordered by graph_score
fused         = RRF(ranks_vector, ranks_graph, k=60)
hits          = top-N(fused), where graph docs get weight w_graph = 0.25 * (1 / hop_depth)
dedupe        = by normalized address / tx_hash / contract_id across lanes
```

Graph evidence is **additive**: it ranks beside vector hits but is explicitly labeled
`source="graph"` so the bot LLM can weigh it. If the graph lane returns nothing (empty anchors,
Neo4j down, timeout), fusion degrades to pure RRF of the vector lane — identical to today.

### 2.5 Bot's 5 collections mapping (normative)

- `defi_exploits` and `scam_patterns` are the **primary graph-feeding collections**: their payload
  metadata (`tx_hash`, `protocol`, `address`, `label`) seeds the most graph anchors.
- `security_patterns` maps 1:1 to `VulnerabilityClass` via `vuln_id`/SWC ids — a vector hit on
  "reentrancy" can surface the graph's `(Contract)-[:VULNERABLE_TO]->(VulnerabilityClass
  {vuln_id:"SWC-107"})` neighborhood.
- `contract_security` anchors `Contract` nodes with verdict metadata.
- `codebase_reference` and `news` stay vector-only (no meaningful graph edges yet).

---

## 3. Ingestion Flow — blacklist/phishing/incident sources → graph edges

Sources already wired in rugbrain's ingest pipelines `[verified: DATA-STRATEGY.md §3, §5;
`src/rugbrain/ingest_sources/blacklists.py`]`. The graph ingest is a **sidecar to the existing
Qdrant ingest**: every ingested doc that carries address/tx/protocol/vuln metadata also MERGEs
graph nodes/edges. A new rugbrain-side script `scripts/ingest_graph.py` (Phase 0) runs on the
same timers as the vector ingest and writes to Neo4j over Bolt (rugbrain holds a Neo4j read/write
client for ingestion ONLY — retrieval reads stay in rmi-backend; alternatively the writer lives in
rmi-backend — final placement decided at implementation, both satisfy the contract).

| Source | Nodes MERGEd | Edges created |
|---|---|---|
| **ScamSniffer** (`scam-database` address/domains json) | `Wallet` (scam addresses), `Domain` (phishing domains), `Label`(`phishing`) | `(Wallet)-[:LABELED_AS]->(Label)`, `(Domain)-[:HOSTS]->(Wallet)` |
| **CryptoScamDB** (blacklist repo, frozen 2022 — historical flag per COLLECTION_REGISTRY) | `Wallet`, `Contract`, `Domain` | `(Contract)-[:FLAGGED_BY]->(BlacklistSource{source_id:"cryptoscamdb"})`, `(Wallet)-[:LABELED_AS]->(Label)` |
| **Forta phishing labels** (P1 — verify endpoint) | `Wallet` | `(Wallet)-[:LABELED_AS {source:"forta"}]->(Label{category:"phishing"})` |
| **ApeSec rugpull registry** (P1) | `Contract`, `Label`(`rugpull`) | `(Contract)-[:FLAGGED_BY]->(BlacklistSource{source_id:"apesec"})`, `(Contract)-[:VULNERABLE_TO]->(VulnerabilityClass{category:"business_logic"})` |
| **DeFiHackLabs / Rekt / SlowMist** incident docs | `TransactionHash`, `Contract`, `Protocol`, `VulnerabilityClass` | `(Wallet)-[:EXECUTED]->(Tx)-[:EXPLOITED]->(Contract)`, `(Contract)-[:VULNERABLE_TO]->(VulnerabilityClass)`, `(Contract)-[:BELONGS_TO]->(Protocol)` |
| **MetaMask eth-phishing-detect** (~107 K domains) | `Domain` | `(Domain)-[:FLAGGED_BY]->(BlacklistSource{source_id:"metamask"})` |
| **rmi-backend label sync** (ClickHouse `wallet_labels`) | `Wallet`, `Label` | `(Wallet)-[:LABELED_AS]->(Label)` — ALREADY LIVE, reused |
| **deployer matcher** (`cross_chain_tracking`) | `Wallet`, `Entity` | `SAME_AS`/`BELONGS_TO` — ALREADY LIVE, reused |

**Idempotency:** every node/edge MERGE is keyed on the natural key (contract_id, tx_hash,
vuln_id, source_id, wallet_id) — re-runs upsert in place. **Ordering:** the vector ingest and
graph ingest run in the same cron tick (vector first, graph second) so the graph never references
a doc Qdrant doesn't have.

---

## 4. Latency Budget — staying inside the 5 s bot RAG budget

Budget source: `RAG_SEARCH_TIMEOUT = 5.0` (seconds) in `app/routers/chat/_rag.py` `[verified]`;
the chat path also gates context at `RAG_CONTEXT_MAX_CHARS = 1800`.

### 4.1 Strategy: pre-computed summaries + bounded live traversal + cache

1. **Pre-computed multi-hop summaries (primary, Phase 1+):** a nightly job walks the scam graph
   and materializes, for each high-degree/flagged wallet and contract, a **bounded subgraph
   summary** into Redis `rag:graph:subgraph:{wallet_id}` (TTL 24 h):
   - depth-1 edges (labels, blacklist flags, kits, deployer links) always included;
   - depth-2/3 edges included only when ≤ 25 nodes (else stored as truncated + pointer).
   - Query-time graph lane reads this cache FIRST (O(1) Redis GET) — no live traversal in the
     common case.
2. **Bounded live traversal (fallback on cache miss):** only when the cache misses does the
   graph lane run live Cypher, with hard caps:
   - depth ≤ 3, `LIMIT 25` per anchor, ≤ 3 anchors per query;
   - **deadline: `asyncio.wait_for(graph_lane(), timeout=1.0)`** — on timeout, return cached
     partial if any, else empty (fail-open, §0.4);
   - Neo4j driver `connection_timeout=10`, per-query timeout set on session config.
3. **Caching layers** (all existing Redis patterns in rugbrain/rmi-backend):
   - `rag:graph:subgraph:{wallet_id}` — subgraph summaries, TTL 24 h (above);
   - `rag:graph:anchor:{query_hash}` — full graph-lane result per query, TTL 1 h;
   - existing semantic query cache (vector lane) unchanged.

### 4.2 Budget table (worst case, P50/P95 typical in parentheses)

| Stage | Budget | Notes |
|---|---|---|
| Embedding (query, BGE-M3 via Infinity) | 0.4 s (0.15–0.3) | warm model; cold start handled by existing keep-alive |
| Vector lane: 5 collections × top-K in parallel | 0.8 s (0.3–0.5) | `asyncio.gather` fan-out, unchanged |
| Graph lane: Redis subgraph hit | 0.01 s | O(1) GET — the common case |
| Graph lane: live traversal (cache miss) | ≤ 1.0 s (0.2–0.5) | hard `wait_for` deadline; runs CONCURRENTLY with vector lane |
| Fusion (RRF + dedup) | 0.01 s | in-process |
| Optional rerank (Qwen3-Reranker-8B, ≤ 25 candidates) | 0.3 s | only if budget remains |
| **Total (worst case)** | **≈ 2.5 s** | parallel lanes: max(vector, graph) + fusion, not sum |

**Why it stays under 5 s:** the graph lane runs concurrently with the vector lane, so the wall
clock is `max(vector, graph)` ≈ 1.0 s + 0.7 s overhead ≈ 1.7–2.5 s worst case, leaving ≥ 2.5 s
headroom for LLM generation inside the bot's RAG window. The pre-computed subgraph cache makes
the common case ~1.2 s total.

---

## 5. Query Set (exact Cypher, Phase 1)

All live queries are bounded and use the constraints from §1.3.

**Q1 — anchor by wallet/contract address (identity):**
```cypher
MATCH (w:Wallet {address: $addr}) RETURN w
UNION
MATCH (c:Contract {address: $addr}) RETURN c
```

**Q2 — scam motif traversal from a wallet (the canonical path, depth ≤ 3):**
```cypher
MATCH p = (w:Wallet {address: $addr})-[r:EXECUTED]->(t:TransactionHash)
         -[:EXPLOITED]->(c:Contract)
OPTIONAL MATCH (c)-[:FLAGGED_BY]->(b:BlacklistSource)
OPTIONAL MATCH (c)-[:VULNERABLE_TO]->(v:VulnerabilityClass)
RETURN p, b.source_id, v.vuln_id,
       reduce(conf = 1.0, e IN relationships(p) | conf * coalesce(e.confidence, 1.0)) AS graph_score
ORDER BY graph_score DESC LIMIT 25
```

**Q3 — contract neighborhood (labels, kit, protocol, vulns):**
```cypher
MATCH (c:Contract {address: $addr})
OPTIONAL MATCH (c)-[:FLAGGED_BY]->(b:BlacklistSource)
OPTIONAL MATCH (c)-[:VULNERABLE_TO]->(v:VulnerabilityClass)
OPTIONAL MATCH (c)-[:USES_KIT]->(k:DrainerKit)
OPTIONAL MATCH (c)-[:BELONGS_TO]->(p:Protocol)
RETURN c, collect(DISTINCT b.source_id) AS flags,
       collect(DISTINCT v.vuln_id) AS vulns, k, p LIMIT 25
```

**Q4 — entity expansion (reuse existing catalog `resolve_entity` traversal):**
```cypher
MATCH (start:Wallet {wallet_id: $wallet_id})
OPTIONAL MATCH path = (start)-[:SAME_AS|FUNDED_BY_SAME|CLONE_OF|BEHAVIORAL_MATCH*1..3]-(other:Wallet)
WHERE start <> other
RETURN other.wallet_id, reduce(conf = 1.0, r IN relationships(path) | conf * r.confidence) AS path_conf
ORDER BY path_conf DESC LIMIT 25
```
(identical to `app/catalog/service.py:resolve_entity` — reused verbatim, NOT reimplemented)

**Q5 — shared-label clustering (reuse existing `find_related_wallets`):**
```cypher
MATCH (w:Wallet {address: $address})-[r:LABELED_AS]->(l:Label)<-[r2:LABELED_AS]-(related:Wallet)
WHERE related.address <> $address AND r.confidence >= $min_conf AND r2.confidence >= $min_conf
RETURN related.address, collect(DISTINCT l.name) AS shared_labels, count(DISTINCT l) AS overlap
ORDER BY overlap DESC LIMIT 50
```

---

## 6. Phased Rollout

### Phase 0 — Reuse existing Neo4j + add edges (no retrieval change)
- [ ] Add §1.3 constraints/indexes (idempotent, run via existing `ensure_schema` pattern).
- [ ] Implement `scripts/ingest_graph.py`: MERGE `Contract`, `TransactionHash`,
      `VulnerabilityClass`, `Protocol`, `DrainerKit`, `BlacklistSource`, `Domain` + edges from the
      §3 sources; reuse existing `neo4j_writer` helpers for `Wallet`/`Entity`/label edges.
- [ ] Wire into existing timers (scam daily refresh, knowledge weekly) alongside vector ingest.
- **Acceptance:** Neo4j contains ≥ 3 sources' flag edges; zero new services; zero retrieval
  changes; `gitleaks detect --no-git` clean.

### Phase 1 — Multi-hop retrieval (graph lane live in `/api/v1/rag/v2/search`)
- [ ] New rmi-backend module `app/domains/rag_graph/` (readers, §5 queries, anchor extraction,
      graph-evidence serializer, Redis subgraph cache, `wait_for` deadline).
- [ ] Extend `SearchResponse` backward-compatibly; fusion per §2.4.
- [ ] Nightly subgraph-summary job (§4.1.1).
- **Acceptance:** bot `/research` and chat return `source="graph"` hits with `graph_summary`
  when anchors exist; p95 search latency ≤ 3 s; Neo4j-down → zero graph hits, vector lane
  unchanged; rerun of identical query hits Redis subgraph cache.

### Phase 2 — Community summaries / entity-level GraphRAG
- [ ] Nightly label-propagation community detection over the scam subgraph (port of the
      deleted `graph_rag.py` community-detection semantics to Neo4j: community = maximal
      connected component of wallets sharing `USES_KIT`/`EXECUTED`/`LABELED_AS`(phishing) edges).
- [ ] LLM-generated **community narrative summaries** stored as Qdrant docs in a new
      `graph_communities` collection (or `threat_intel` with `category="community"`) — this is
      where community text becomes *semantically* retrievable.
- [ ] Entity-level retrieval: an anchor wallet first fetches its community summary doc
      (vector lane), then the community's member wallets (graph lane Q4/Q5) — "show me the ring
      around this address".
- **Acceptance:** community detection over ≥ 10 K wallet graph returns stable clusters
  (rerun-stable IDs); `/investigate` on a flagged wallet surfaces its community + narrative;
  RAGAS eval shows recall gain on scam-network golden queries.

---

## 7. Acceptance Gates (all phases)

1. `grep -rn "graph_rag\|app.rag_graph" src/rugbrain/` → zero matches (module deleted); rugbrain
   has NO graph import except the ingest writer decision documented in §3 (retrieval reads live
   in rmi-backend only).
2. All new Cypher bounded (`LIMIT` on every traversal) — no unbounded `MATCH (n)` in query paths.
3. Latency: p95 of `POST /api/v1/rag/v2/search` ≤ 3 s with graph lane on; ≤ 2 s with Neo4j down.
4. Fail-open proven: `docker stop rmi-neo4j` → search still 200 with zero `source="graph"` hits.
5. Idempotent graph ingest: run twice → second run creates 0 new nodes/edges.
6. `gitleaks detect --no-git` clean; no new services, no new MCPs (per AGENTS.md pushback rules).
7. Docs updated: this file is the contract; COLLECTION_REGISTRY.md gains `graph_communities`
   entry when Phase 2 lands.

---

*End of design. This document is the authoritative spec for GraphRAG in rugbrain. The legacy
Redis-based module (`src/rugbrain/graph_rag.py`) and its `GET /api/v1/rag/graph-communities`
route were **deleted** (`aadf85f`, 2026-08-16) — no code imports it, and its `synthesize_answer`
shim lives on in `src/rugbrain/ratings.py`. Its *capabilities* are re-implemented over Neo4j
per this design.*
