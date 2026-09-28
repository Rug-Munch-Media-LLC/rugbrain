# RugBrain MCP — security posture

<!-- SPDX-License-Identifier: MIT -->
<!-- Copyright (c) 2026 Rug Munch Media LLC -->

Threat model for exposing RugBrain's crypto-crime corpus as an MCP tool
surface. Written 2026-09-28, before the surface is live.

---

## 0. The one risk that dominates the rest

**A crime RAG is attacker-authored data.** Every phishing report quotes the
scammer's page. Every post-mortem quotes the exploit PoC. Telegram channels
*are* the adversary. This system ingests hostile text by design — that is
what makes it useful.

The moment ingested text reaches an LLM with its instructions intact, the
attacker owns the agent, and the agent holds keys, a shell, and the ability
to write into the very corpus that is supposed to be trustworthy.

So the primary control is not detection (adversarial, unreliable) but
**neutralization** (deterministic, does not depend on winning an arms race),
applied in two directions in `src/rugbrain/digest/security.py`:

| Direction | Function | What it does |
|---|---|---|
| model-in | `neutralize()` | Strips zero-width/bidi chars, NFKC-normalizes, wraps in `<<<UNTRUSTED_DOCUMENT_BEGIN>>>`, neutralizes instruction-shaped phrases |
| model-out | `scrub()` | Strips invisible chars, redacts secret-shaped substrings |
| triage | `has_injection()` | Signals for logging. **Never** used to silently drop a document |

`has_injection()` is explicitly *not* a gate. The phrase "ignore all previous
instructions" is frequently quoted verbatim in a scam report — that document
is exactly the one an investigator needs. Dropping it would destroy the
evidence while failing to stop a determined attacker.

The default extraction path is regex-only and **never calls a model**, so the
in-boundary is not live until a summarizer is added. Keep it that way, or
route every model call through `neutralize()`.

---

## 1. Tool allowlist — the exposed surface

Four tools. Each is a **read** except where explicitly marked.

| Tool | Class | Namespace | Rationale |
|---|---|---|---|
| `scam_lookup` | read | general | Address → known-scam verdict. The core product. |
| `wallet_profile` | read | general | Multi-hop entity graph, bounded `max_hops`. |
| `exploit_search` | read | general | Semantic search over `hacks_v2` / `threat_intel_v2`. |
| `rug_events` | read | general | Time-bounded rug/exploit event feed. |
| `digest_document` | **WRITE** | **operator only** | Ingests a document. Writes Qdrant + Neo4j. |
| `recall_memory` | **WRITE** | **operator only** | Persists a durable agent memory. |

```jsonc
// deny-list in opencode.jsonc "tools", AFTER the server block
"metamcp-rugbrain_digest_document": false,
"metamcp-rugbrain_recall_memory": false
```

**Why writes are denied by default.** A read-only surface leaks what the
fleet already knows. A write surface lets any agent with access **inject
false intelligence into the crime corpus** — plant an address as "verified
safe" or a protocol as "not exploited" — which then propagates into every
downstream blocklist and every agent that recalls it. For a system whose
entire value is trust in its verdicts, that is the highest-severity failure
available, and it requires no exploit.

Verify the deny-list actually bites (runbook V3): if the denied call
succeeds, the surface is too wide regardless of what the config says.

---

## 2. Auth

| Layer | Control |
|---|---|
| metamcp → client | `Authorization: Bearer {file:/srv/metamcp/.fleet-agents-key}`, file mode `0600` |
| client → RugBrain MCP | Loopback bind only (`127.0.0.1:9099`). Never `0.0.0.0`. |
| MCP → Qdrant | Scoped API key, 4 collections only |
| MCP → Neo4j | Scoped user, `MATCH {group_id:'rugbrain'}` grant |

The bearer key is a **fleet-agents** credential — it already fronts
`metamcp-code`, `ops`, `audit` and `research`. Do not mint a second one for
RugBrain; reuse the file reference so there is exactly one key to rotate.

Never use the full `neo4j` user: it can read the corpus-taxonomy nodes
sharing that instance, which is a different data domain.

---

## 3. Input limits

Every unbounded parameter is a denial-of-service vector, not a style
preference.

| Parameter | Cap | Enforced in |
|---|---|---|
| `content` (ingest) | 200K chars to extract, 20K stored | `entities.extract`, `graph.write_episode` |
| Summary input | 8K chars | `security.MAX_SUMMARY_CHARS` |
| Memory content | 1,200 chars | `memory.MAX_MEMORY_CHARS` |
| Graph read `limit` | 100 | `find_entity_neighbors` |
| Memory recall `limit` | 100 | `InMemoryMemoryStore.search` |
| Collection routing | 4 names, allowlisted | `WRITABLE_COLLECTIONS` |
| Relationship type | 4 names, allowlisted | `_ALLOWED_RELS` |
| `scrub_recursive` depth | 12 | guards against self-referential payloads |

`WRITABLE_COLLECTIONS` and `_ALLOWED_RELS` are both **allowlists, not
denylists**, and both are load-bearing:

- Without the collection allowlist, a caller could pass a collection name and
  write into `codebase_reference_v2` or any other collection in this Qdrant
  instance — the Qdrant HTTP API has no per-client scoping unless keys are
  scoped, so this is the only in-process boundary.
- Relationship type **cannot** be a Cypher parameter, so it is interpolated.
  It is substituted from a fixed allowlist via literal token replacement —
  **not** `str.format`, which would interpret the query's own `{}` braces.
  A test pins both the rejection and the brace preservation.

---

## 4. Returned content is still untrusted

A tool that returns ingested text to an agent is a **rehydration** channel:
data stored last week is delivered to the model's context today. `scrub()` is
applied recursively to tool output. It does not neutralize instruction
phrases — the client is an agent with its own boundary handling, and
rewriting a scam report would corrupt evidence. The model-in boundary is
`neutralize()`'s job.

---

## 5. What RugBrain must never expose publicly

| Never expose | Why |
|---|---|
| Unfiltered `digest_document` on a public endpoint | Anyone can write false intelligence into the corpus |
| The Neo4j full user, or an unscoped Qdrant key | Cross-domain read: corpus taxonomy, and every other collection |
| `wallet_profile` with unbounded `max_hops` | An unbounded walk off a hot entity node is a resource-exhaustion and data-exfiltration primitive |
| Internal collection names + counts to unauthenticated clients | Free map of the intel estate |
| Raw ingestion sources with upstream credentials in metadata | Leaks how the corpus was obtained |
| A B2 key on this surface | That is the backup-verification path. Storage credentials and crime-intel queries have no reason to share a trust boundary |
| Embedding/rerank API keys via env passthrough | Classic MCP exfil target |
| Live scammer-linked PII (doxxing, victim identities) | Legal exposure; a public crime index attracts it |

**Network posture:** bind `127.0.0.1` only. Anything remote goes through
metamcp, which is where the bearer key and the allowlist already live. A
direct public bind bypasses every control in this document.

---

## 6. Threat summary

| # | Threat | Severity | Control | Residual |
|---|---|---|---|---|
| S1 | Poisoned ingest becomes a stored false verdict | **Critical** | Write tools denied by default; `should_remember` strictness; `WRITABLE_COLLECTIONS` | Requires an operator-enabled namespace |
| S2 | Stored injection rehydrated into a future session | **Critical** | `scrub()` on all output; `neutralize()` on any model call; `has_injection` blocks memory write | A summarizer added without `neutralize()` reopens this |
| S3 | Secret exfiltration via tool output | High | `scrub()` redacts 6 secret shapes | Custom token formats can evade a regex |
| S4 | Cypher injection via relationship type | High | `_ALLOWED_RELS` allowlist + token replace | None known |
| S5 | Resource exhaustion via unbounded walk | High | `limit` caps; `max_hops` bound | Per-request cost still uncapped at the gateway |
| S6 | Key exposure through the MCP surface | High | Loopback-only; scoped keys; no B2 key | A fleet-agents key is shared across 5 namespaces |
| S7 | Invisible-character smuggling | Medium | `strip_invisible()` + NFKC on both paths | Unicode homoglyphs survive normalization by design |
| S8 | Graph poisoning via re-ingest | Medium | Deterministic `stable_key` → idempotent MERGE | Requires operator write access |

---

## 7. What is deliberately NOT built

Stated plainly so a later reader does not mistake omission for oversight.

- **No prompt-injection classifier.** Unreliable under adversarial input, and
  it produces false confidence. Neutralization is deterministic.
- **No public HTTP API.** Every control here assumes a trusted caller.
- **No arbitrary Cypher tool.** An agent that can run Cypher can delete the
  graph and call that a retrieval.
- **No autonomous ingest loop.** A human decides what enters the corpus.
