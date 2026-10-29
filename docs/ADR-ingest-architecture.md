# ADR — Ingest architecture: shared fetch / normalise / store + source registry

- **Status:** accepted, slice 1 landed
- **Date:** 2026-10-25
- **Scope:** `src/rugbrain/ingest_sources/` (149 modules, ~21.3k lines)
- **Slice migrated:** `papers` family (`papers.py`, `papers_norm.py`)
- **Enforced by:** `tests/test_arch_ingest_layers.py`

## 1. Problem — measured duplication (before)

`ingest_sources/` grew source-by-source. Every family re-implemented the same
five concerns instead of sharing them.

| Concern | Sites | Concrete examples |
|---|---|---|
| HTTP client construction | **9** | `base.py:96`, `papers.py:150`, `telegram_web.py:143`, `x_intel/client.py:86`, `x_intel/pry_lane.py:82`, `discover_joined_channels.py:116`, `discover_scam_channels.py:88`, `docling_client.py:90` |
| User-Agent / proxy literal | **24** | `base.py:35`, `x_intel/client.py:57`, `papers.py:150` (no UA at all), `telegram_web.py:143` |
| Auth header formatting | **4** | `chainabuse.py:210`, `x_intel/client.py:57`, `x_intel/pry_lane.py:63`, `github.py:52` |
| Retry / backoff | **1 ad hoc** | `blacklists.py:350` (`for attempt in range(2)`) — the other 65 HTTP callers have none |
| Fixed sleeps (rate limit stand-in) | **5** | `asyncio.sleep` spread across telegram + x_intel lanes |
| Direct `httpx` imports | **66 files** | every family; after slice 1: **65** |
| Direct store / Qdrant imports | **5 files** | `telegram_userbot.py:405`, `registry_contract.py:76`, `registry_scan.py`, `docling_ingest.py`, `scythe_radar_datasets.py` |
| Normalise shapes | **3** | `base.IngestDoc`, `papers_norm.SourceDoc`, `content_sources` plain `dict` items |
| Registry | **1 central 60-import list** | `registry.py:57-120` — adding a source edits the catalogue |

Failure modes that follow: a source without a UA gets blocked and returns `[]`
indistinguishably from "empty"; a 429 is never retried (blacklists is the only
one that tries); credentials are formatted four different ways; a new source
must edit `registry.py`, so families drift apart.

## 2. Decision — four shared layers, one registry

```
source module (one file, one entry)
        │  fetch(client, limit)
        ▼
shared.http.client()        ← only httpx construction site
shared.http / shared.retry  ← UA, proxy, timeout, backoff, 429/5xx
shared.auth.bearer()        ← only Authorization formatting site
        │  returns documents
        ▼
shared.normalise.Normalised ← content + doc_id + metadata (Protocol)
        │  stable_doc_id(source, key, content)
        ▼
shared.store.StorePort      ← only storage port; FirehoseStore adapter
        │
        ▼
firehose.pipeline.IngestionPipeline (one embedding path, 4096-d)
```

**Rules**

1. A source imports `shared.*`. It does not import `httpx`, `aiohttp`,
   `requests`, `qdrant_client` or `rugbrain.qdrant_store`.
2. A source receives an `httpx.AsyncClient`; it never constructs one.
3. Documents satisfy `shared.normalise.Normalised`; ids come from one rule.
4. Writes go through `StorePort`; a source never touches a vector store.
5. Adding a source = **one module + one `register(...)` entry**. No central
   import list.
6. The gate is a **ratchet**: legacy violations are frozen in
   `tests/arch_allowlist_httpx.txt` (65) and `arch_allowlist_qdrant.txt` (5),
   and may only shrink. `>64` or `>8` fails CI.

**Rejected:** a single god `IngestClient` class (re-creates the coupling
`base.Source` already has), and rewriting all 149 modules at once (no review
surface, no rollback). Families migrate one at a time; the allowlist is the
migration ledger.

## 3. Registry

```python
register("papers", family="papers", fetch=harvest,
         doc_type="standard", collection=COLLECTION)
```

`shared/registry.py` maps name → `SourceEntry(name, family, fetch, doc_type,
collection)`, rejects duplicate names, and answers `get()` / `names()` /
`by_family()`. It coexists with `ingest_sources/registry.py` (the `Source` ABC
catalogue) during migration; that central import list shrinks family by family
as each entry moves to its own module.

## 4. Slice 1 — `papers` (landed)

Chosen because it is the smallest family with real network I/O and its tests
(`tests/test_ingest_papers_harvest.py`, `test_ingest_papers_norm.py`) inject the
client, so behaviour must stay identical.

| Change | Before | After |
|---|---|---|
| HTTP client | `httpx.AsyncClient(timeout=30.0, follow_redirects=True)` at `papers.py:150` | `shared.http.client(timeout=30.0)` — UA + proxy for free |
| `import httpx` | yes | **no** (annotations are `Any`) |
| Registry | none | `register("papers", ...)` entry in `papers.py` |
| Duplicated helper | own client construction | deleted; `shared.http` owns it |
| Behaviour | dry-run, deterministic ids, error counters | unchanged |

Not changed on purpose: `papers_norm.point_id` (already deterministic and
tested — one id scheme must win), and the `--execute` refusal (writes still
require the reviewed pipeline).

## 5. Migration plan for the remaining families

Order = least-test-coverage-risk first, allowlist entry deleted in the same PR.

1. **feeds / eng_blogs / players_*** (RSS, 8 modules) → `shared.http` +
   `shared.retry`. Delete 3 UA literals.
2. **blacklists / chainabuse / phishdestroy / openphish / urlhaus** (security
   feeds) → `shared.http` + `shared.retry` + `shared.auth.bearer`
   (`chainabuse.py:210`, `github.py:52`).
3. **content_sources/** (12 modules) → already has a `pipeline`/`spec` split;
   align `spec.SourceSpec` with `shared.normalise`, route its `fetch` through
   `shared.http`, keep `collapse` as-is.
4. **docs / devdocs / players_docs / analytics_docs** (5 modules) → one
   manifest fetch via `shared.retry`.
5. **x_intel/** (20 modules) → already has `client.py`/`routing.py`; make
   `x_intel/client.py` a *consumer* of `shared.http` + `shared.auth`, delete
   its duplicate header builder (`pry_lane.py:63`).
6. **telegram** (`telegram_web`, `telegram_userbot`, `tg_*`) → shared client;
   keep the Telethon boundary (it is not HTTP).
7. **incidents / rekt / crypto_rekts / defihacklabs** (largest modules) last —
   they carry the most parsing logic; only the fetch edge changes.
8. **firehose / prediction / store call sites** → `StorePort`; delete the
   remaining 5 direct Qdrant imports; `registry_contract` keeps read-only
   access behind `shared.store`.

Each step: delete the module's allowlist line → CI proves no regression → the
ratchet drops. Target after the sweep: 0 HTTP / 0 store allowlist entries.

## 6. Consequences

- **Good:** one place to fix UA/429/proxy; a new source is one file; storage
  and embedding have exactly one entry point; the gate fails on violation.
- **Cost:** `Any`-typed client annotations in migrated modules until
  `httpx.AsyncClient` moves behind a `shared.http.HttpClient` alias; the two
  registries coexist until step 8.
- **Risk:** a family that relies on its own timeout/proxy semantics may change
  behaviour — mitigated by migrating only where tests inject the client, and by
  keeping the old module's public functions intact.
