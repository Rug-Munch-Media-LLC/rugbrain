# Adding an Ingest Source to RugBrain

> Step-by-step guide for adding a new programmatic ingest source to RugBrain
> (`src/rugbrain/ingest_sources/`). Read
> [`DATA-PLACEMENT.md`](DATA-PLACEMENT.md) before starting — it is the
> canonical split between this repo and `rmi-codebase-mcp`.

Status: **canonical** · owner=Rug Munch Media LLC Engineering

## Before you start

Two gates:

1. **Placement.** `DATA-PLACEMENT.md` rule: **code → MCP, knowledge → RAG.**
   If your source is code (`.sol` / `.rs` / `.vy` / `.move` snippets), it
   belongs in `rmi-codebase-mcp`, not here. Sources here carry *facts*:
   writeups, post-mortems, blacklists, entities, market data, standards.
2. **Reuse.** The registry in `ingest_sources/__init__.py` already has 25+
   sources. Prefer extending an existing source over adding a new module —
   never duplicate a source.

## Step 1 — pick the target collection

Collection names follow the `*_v2` convention — those are the **real Qdrant
collection names** (see [`COLLECTION_REGISTRY.md`](COLLECTION_REGISTRY.md);
the pre-migration names `threat_intel`, `news`, `scam_intel`, … resolve to
nothing).

- **Reuse an existing collection** when your payload fits one that is live:
  `threat_intel_v2`, `defi_exploits`, `scam_intel_v2`, `research_v2`,
  `contract_audits`, `news_v2`, … Freshness and ownership are tracked per
  collection in the registry.
- **Create a new one** only when the semantics are distinct; name it
  `<semantic>_v2` and add a row to `COLLECTION_REGISTRY.md`.

The collection is carried on each document via
`IngestDoc.metadata["collection"]` and/or the class attribute
`default_collection`.

## Step 2 — the `Source` interface

Every source module implements the small, dependency-free contract in
[`ingest_sources/base.py`](../src/rugbrain/ingest_sources/base.py). There
are exactly two moving parts.

### Class attributes (set on your subclass)

| Attribute | Type | Required | Meaning |
|-----------|------|:--------:|---------|
| `name` | str | ✅ | Unique source name (used by `--source`, `get_source()`). |
| `doc_type` | str | ✅ | Semantic type, e.g. `post_mortem`, `exploit_poc`, `scam_entity`, `audit_finding`, `standard`. |
| `default_collection` | str | ✅ | Qdrant collection this source feeds (`*_v2` name). |
| `description` | str | ✅ | One-line summary (shown by `--list`). |
| `timeout` | float | — | HTTP timeout; default 30.0. |

### One method

```python
async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
```

Contract rules (same as `base.py`):

- **Never raise.** Any failure → `log.warning(...)` + return `[]`. A flaky
  upstream must never abort a batch.
- Respect `limit` — truncate the docs you return (`None` = source default).
- Fetch via the passed `client`; the base class already wires the
  `User-Agent`, redirects, and the optional `PROXY_URL` proxy.

The base `fetch()` wraps `_fetch()` in an `httpx.AsyncClient` and applies the
limit again.

### `IngestDoc`

```python
IngestDoc(
    content="clean text payload (markdown, code, or structured text)",
    doc_id="<source>:<stable key>:<content hash>",
    metadata={"collection": "threat_intel_v2", "chain": "EVM", "severity": "high", ...},
    doc_type="post_mortem",
    source="<your source name>",
    title="Human-readable title",
    url="https://...",
)
```

- `doc_id` must be **deterministic** — the canonical scheme is
  `<source>:<stable key>:<content hash>` (see `_content_hash` in
  `blacklists.py` / `incidents.py`). The store layer converts the seed into
  a UUIDv5 point ID, so re-ingestion overwrites the same points and stays
  **idempotent**. Never use a random UUID or a raw counter.
- `metadata` is the filterable payload: collection, chain, severity, date,
  entity fields, source URL. Mirror existing sources' key names
  (`vulnerability_class`, `protocol`, `loss_usd`, …) so downstream filters
  stay consistent.
- `title` / `url` are optional conveniences mirrored into `metadata` by
  `to_crawl_document`.

## Step 3 — use the GitHub helpers (repo-backed sources)

[`ingest_sources/github.py`](../src/rugbrain/ingest_sources/github.py)
gives every GitHub-backed source its two calls:

- `list_repo_files(client, repo, ref)` — one recursive `git/trees` call
  returns the full file list.
- `fetch_raw(client, repo, ref, path)` — `raw.githubusercontent.com` fetch
  (not subject to the core API rate limit).
- `pick_files(entries, suffixes=..., include=...)` — suffix / directory
  filter.

Rate limits: the unauthenticated GitHub core API allows 60 requests/hour
per IP; set the `GITHUB_TOKEN` env var to lift it to 5000/hour. `PROXY_URL`
routes all traffic through a residential proxy when GitHub rate-limits the
origin IP. Both are environment-only — never hardcode credentials.

## Step 4 — register the source

[`ingest_sources/__init__.py`](../src/rugbrain/ingest_sources/__init__.py)
holds the registry:

1. Import your source class(es).
2. Add an instance to the `SOURCES` tuple.
3. (Optional) add the name + doc_type to the module docstring's source list.

That is all — `--list`, `get_source()`, `fetch_source()`, and `fetch_all()`
are built from `SOURCES` automatically, and `DOC_TYPES` is derived from the
same tuple.

## Step 5 — add a test

Tests live in [`tests/test_ingest_sources.py`](../tests/test_ingest_sources.py)
(pytest + `respx` for HTTP mocking). Three layers, all expected for a new
source:

1. **Registry test** — add `"<your_source_name>": "<doc_type>"` to the
   `REQUIRED` dict. This asserts registration + semantic-type wiring.
2. **Parser test** — pure-function test of your normalizer (no network):
   feed a small fixture string, assert the `IngestDoc` shape, `doc_id`
   determinism, `doc_type`, and key metadata fields. Most sources export a
   `parse_*` / `*_to_doc` pure function for exactly this.
3. **Fetch test (recommended)** — `@respx.mock` the raw/API URLs and assert
   `fetch()` returns docs without raising.

Run:

```bash
python -m pytest tests/test_ingest_sources.py -q
```

## Step 6 — run it

```bash
python -m rugbrain.ingest_sources --list                      # see your source
python -m rugbrain.ingest_sources --source <name> --limit 3   # smoke fetch
```

The full store pipeline (chunk → embed → Qdrant) is separate: bridge your
`IngestDoc`s with `to_crawl_document(doc)` (base.py) and hand them to
`ingest_common.store_documents` — or wire your source into whichever script
/ cron owns its collection.

## Worked examples

- [`blacklists.py`](../src/rugbrain/ingest_sources/blacklists.py) —
  multi-feed aggregation: a `Feed` descriptor per upstream list, one
  `IngestDoc` per address/domain entity, deterministic
  `<source>:<feed>:<entity_type>:<hash>` doc_ids, fail-open per feed,
  targeting `scam_intel_v2`.
- [`incidents.py`](../src/rugbrain/ingest_sources/incidents.py) — four
  incident sources in one module, each with a pure parser
  (`parse_guttedefi_table`, `parse_immunefi_hack_analyses`, …) + `*_to_doc`
  normalizer + a thin `Source` subclass. Note the collection routing:
  post-mortems → `defi_exploits`; SlowMist audit reports → `contract_audits`
  (per `DATA-PLACEMENT.md`).

## Checklist

- [ ] Placement verified: this is **knowledge**, not code (`DATA-PLACEMENT.md`)
- [ ] No existing source already covers this feed
- [ ] Target collection exists (`*_v2` name) or was added to `COLLECTION_REGISTRY.md`
- [ ] `Source` subclass sets `name`, `doc_type`, `default_collection`, `description`
- [ ] `_fetch` never raises; returns `[]` + warning on failure
- [ ] `doc_id` deterministic (`<source>:<key>:<content-hash>`) → idempotent
- [ ] Credentials env-only (`GITHUB_TOKEN`, `PROXY_URL`) — none hardcoded
- [ ] Registered in `ingest_sources/__init__.py` (`SOURCES` tuple)
- [ ] `REQUIRED` entry + parser test + (recommended) respx fetch test added
- [ ] `python -m rugbrain.ingest_sources --source <name> --limit 3` works
