# RETRIEVAL MAP

**How a rugbrain query reaches an answer, and where the collection name is
decided.** One page, on purpose. Every width below is MEASURED against live
Qdrant `http://rmi-qdrant-v2:6334` on 2026-09-29 (96 collections, 33 `_8b`
twins) — not declared, not aspirational. Re-measure with the one-liner in
[§6](#6-how-to-re-measure).

---

## 1. The canonical lane, and where it is declared

| Fact | Value | Declared in |
|---|---|---|
| Canonical lane | **4096** (Qwen3-Embedding-8B) | `qdrant_store/lane_table.py` → `CANONICAL_LANE_DIM` |
| Canonical width, MEASURED | **4096** | `qdrant_store/lane_widths.py` → `TWIN_LANE_DIM` |
| Preserved/retired lane | **2560** (Qwen3-Embedding-8B, `:8004`) | `lane_table.PRESERVED_LANE_DIM` |
| 1024 collections | 6 of them, pinned | `lane_table.PINNED_DIMS` |
| **The choke point** | `resolve_collection(base) -> base_8b` | `qdrant_store/lane_resolver.py` |

`TWIN_LANE_DIM` is a hardcoded `4096`, **not** read from `CANONICAL_LANE_DIM`.
That split is deliberate (`lane_widths.py` explains why): `CANONICAL_LANE_DIM`
reads `RAG_EMBED_DIM`, so if the operator env pins 2560 the *declaration* moves
to 2560 and starts lying about a collection that is physically 4096.

**Live hazard, measured today:** `dim_policy.COLLECTION_DIM` = **4096**, but
`query_embedder.embed_query()` returns **2560** and
`embeddings.get_embedding()` returns **4096**. Both are correct — they are two
different lanes. "The embedder" is not a single thing here.

---

## 2. The four collection groups (measured)

| Group | Count | Width | Example | Rule |
|---|---|---|---|---|
| **Written** (`_8b` twins) | 33 | 4096 | `threat_intel_v2_8b` | Resolver target. `lane_table.DECLARED_TWINS` (21 owned) |
| **Read-only retained** (bases) | 22 | 2560 | `threat_intel_v2` | Never written. Resolver rewrites to the twin |
| **Already canonical** (no twin) | 10 | 4096 | `privacy_protocol` | No rewrite needed. `lane_widths.CANONICAL_BASES` |
| **Pinned below canonical** | 6 | 2560 (4) / 1024 (2) | `rug_events`, `wallet_clusters` | **Deliberate exception** — never a 4096 write target |
| **External** (not ours) | 25 | mixed | `codebase_reference_v2`, `exploits` | Pass through untouched. Write-ownership boundary |

Measured width histogram across all 96: **4096 × 48, 2560 × 42, 1024 × 6**.

> 12 of the 25 "external" names have live `_8b` twins (`exploits_8b`,
> `master_skills_8b`, …) that are **deliberately not declared**. Routing a
> rugbrain source at them would widen write ownership past
> `owned_registry._RMI_RAG_OWNED_COLLECTIONS`.

**Both generations are populated and both are correct for their own width.**
Verified by search, live:

```
defi_exploits      @2560 -> 200   @4096 -> 400   (base, retained)
defi_exploits_8b   @2560 -> 400   @4096 -> 200   (twin, written)
```

So resolving a name is only **half** the job — the embedder must match whatever
the name resolved *to*. Doing one without the other is a regression, not a fix.

---

## 3. Every path from text to answer

```
                          TEXT
                            |
   (A) PRODUCTION SEARCH ---+---> query_intent.resolve_search_collections
   routes/search_retrieval        (CHOKE POINT #1)  search/lane_targets.py:64
   search/engine/multi_coll         |
   search/engine/dense_pillar       v
   search/engine/transform     search/engine/vector_lane.search_similar
                                (CHOKE POINT #2, last point before the name
                                 reaches the embedder)  vector_lane.py
                                    |
                          qdrant_store.store_search.SearchMixin.search
                                    |
                          namespace.unified_route -> map_collection
                                (CHOKE POINT #3)  namespace.py:76
                                    |
                            resolve_collection  <-- THE choke point
                                    |
                                  QDRANT

   (B) DIRECT EVAL -----------+---> eval/qdrant_retriever.retrieve
   eval/retrieval_eval            (CHOKE POINT #4)  <-- fixed 2026-09-29
   eval/query_scoring             |
                            resolve_collection, then embed on the
                            LIVE width of what it resolved to

   (C) MISSION EVAL ----------+---> missioneval/collection_retriever
   (24915db)                     (CHOKE POINT #5)  same two-step rule

   (D) RAW HTTP (bypass all of the above)
   crypto_code_pkg/retriever      CONTEXTCRYPTO_COLLECTIONS only — external
   ingest_smart, multi_embedder   deliberate external reads, see §5
```

**One resolver, four entry points.** `search/lane_targets.py` adds no table and
no suffix of its own — it is a thin pass-through, so the read side and the
write side cannot drift again.

---

## 4. What happens today when a caller does NOT resolve

This is the failure that has cost this project time three times. It is
**silent**, which is the whole problem:

| Caller state | Qdrant response | Eval records | Looks like |
|---|---|---|---|
| Resolves, embedder matches | 200, real hits | score | working |
| **No resolve, 4096 vec, 2560 base** | **400 dim error** | **`QueryScore.error`** | **"corpus lacks the answer"** |
| Resolves, embedder still 2560 | 400 dim error | `QueryScore.error` | same, and it is a *regression* |
| Name is a **typo** | 404 → guard **creates** a fresh empty 4096 collection | 0 hits, no error | "empty corpus" |

The last row is the worst and is documented in `lane_resolver.py`: an
**unlisted name is not a lane error.** `resolve_collection` passes it through,
and `_ensure_collection(create_if_missing=True)` silently forks a new empty
corpus. "The guard will catch it" is true for declared names and **false for a
typo**.

Three prior instances, same bug, different directory:
`be0da01` (search path) · `24915db` (mission-eval retriever, 68 errors /
gold_rate 0.0 → 0.765) · **this lane** (eval retriever).

---

## 5. Deliberate exceptions — and how to tell one from a bug

**A deliberate exception is commented and states why. A bug is silent.** That
distinction is the point; do not "fix" the list below without reading the
reason attached to it.

| Caller | Why it does not resolve | Verdict |
|---|---|---|
| `crypto_code_pkg/retriever.py` | Searches only `CONTEXTCRYPTO_COLLECTIONS` (`codebase_reference_v2`, `security_patterns`) at their own 2560 width. None is an owned declared base; `security_patterns` has a twin but is outside write ownership. | **DELIBERATE** — external corpus, own lane |
| `eval/verification.py` | Never touches Qdrant. Reads a JSON evidence sidecar (`verification.json`); `Evidence.collection` is a record field, not a query. | **NOT A CALLER** — listed in error |
| `eval/query_scoring.py` | Calls an injected `retriever` callable. Resolves nothing because it *is* the caller that consumes one. | **NOT A CALLER** — pure scoring |
| `search/engine/{transform,dense_pillar,multi_collection}.py` | Delegate to `search_similar` / `search_multi_collection`, both of which resolve. Re-resolving would be harmless but redundant. | **COVERED DOWNSTREAM** |
| `eval/retrieval_eval.py` | Endpoint mode names a collection to the HTTP API (which resolves server-side); in-process mode calls `search_multi_collection` (resolves). | **COVERED DOWNSTREAM** |
| Pinned 2560 collections (`coding_knowledge`, `solana_patterns`, `rug_events`) | Genuinely 2560, **no `_8b` twin exists**. A 4096 vector can never enter them. | **DELIBERATE** — the eval embeds 2560 for these on purpose |

> **Correction to the original brief.** The scan that produced this lane listed
> all seven files as equally broken. That is not accurate, and the difference
> matters: only `eval/qdrant_retriever.py` sends a name to Qdrant without
> resolving. Two of the seven never contact Qdrant at all, three resolve
> downstream. Treating them as one defect would have produced four no-op edits
> that look like fixes.

---

## 6. How to re-measure

Never assert a width — measure it. Qdrant `:6334` is **production: GET/search
only, never PUT or DELETE.**

```bash
# widths of every collection (list endpoint omits vector config; per-item GET)
curl -s http://rmi-qdrant-v2:6334/collections | python3 -c "
import json,sys,urllib.request
from concurrent.futures import ThreadPoolExecutor
cols=json.load(sys.stdin)['result']['collections']
def f(c):
    n=c['name']
    with urllib.request.urlopen(f'http://rmi-qdrant-v2:6334/collections/{n}') as r:
        v=json.load(r)['result']['config']['params']['vectors']
    return n, (v['size'] if 'size' in v else list(v.values())[0]['size'])
rows=list(ThreadPoolExecutor(8).map(f,cols))
print('total',len(rows)); print([r for r in sorted(rows) if r[0] in
      ('defi_exploits','defi_exploits_8b','coding_knowledge')])"
```

Proving a lane is right is a **search**, not a config read — it is the only
check that catches a name resolving to the wrong width:

```python
# 4096 vector against a 2560 base -> 400. This is the bug, live.
```

If a collection is missing from this map, it is either not owned (do not route
to it) or genuinely absent (do **not** auto-create it — that is how the empty
corpus in §4 gets forked).

---

## 7. Rules for a new caller

1. **Name the collection through `resolve_collection`.** One resolver; no
   second table, no suffix of your own, no fallback.
2. **Then read the LIVE width of what it resolved to** and embed on that lane.
   Resolution alone is a regression — both generations are populated.
3. **Never guess or truncate a width.** An 8B vector squeezed into a 4B index
   searches fine and means nothing. Raise instead.
4. **Never write to a base or a pinned collection.** Bases are retained
   read-only; pinned ones are deliberate exceptions.
5. **If you genuinely target a non-4096 lane, say so in a comment** with the
   reason. A silent exception and a silent bug must not look alike.
6. **`require_resolved` when you *know* your name must resolve** — it is the
   opt-in assertion for a caller that would otherwise pass through unnoticed.

The two rules that were learned the hard way, in one line each:
**a component that decides which collection is in play must resolve through the
same resolver as the component that decides which are written** (that is how
`be0da01` / `24915db` / this lane happened), and **a declaration that moves with
the same env var as the vector it checks is not a declaration** (that is why
`TWIN_LANE_DIM` is a constant).
