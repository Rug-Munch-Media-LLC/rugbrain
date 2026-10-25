# Docs ingestion standard

One registry (`data/corpus/docs_registry.json`) answers, for every documentation set we
hold: what it is, where it came from, under which licence, how fresh it is, and how to
query it. Ad-hoc ingestion is over — a set that is not in the registry is not ours.

## 1. Registry entry (all fields mandatory)

| field | rule |
|---|---|
| `library_id` | `docs:<slug>`, lowercase, stable (it is the query key) |
| `display_name` | human title, from the source's own `<title>`/`h1` |
| `source_repo` | `owner/repo` for GitHub sources, else the host |
| `source_url` | the canonical entry page actually fetched |
| `licence` | SPDX id. `UNKNOWN` is a **validator failure**, never a valid value |
| `version` | tag/commit the set was built from (`source_commit` when available) |
| `page_count_estimate` | distinct pages observed in the sample |
| `chunk_count` | exact point count in Qdrant for the `library_id` |
| `fetched_at` | ISO-8601 of the crawl that produced these chunks |
| `publishable` | true only for permissive licences (MIT/Apache/BSD/CC-BY/CC0/unlicense) |
| `quality` | `reference` / `guide` / `mixed` / `landing` |

Commands: `build` (rebuild from Qdrant), `list [--quality]`, `search <q>`,
`add <entry.json>`, `refresh-status` (fills `fetched_at`, reports the rest),
`validate` (gate).

## 2. Quality gate

A set's quality is **measured**, not declared: up to 40 distinct page URLs are sampled
from its chunks and classified per page (`docs_registry_quality.classify_url`).

- `landing` — root page, `index`, `home`, `overview`, `pricing`, `careers`, `blog`,
  `news`, `changelog`, `releases`
- `reference` — `/api`, `reference`, `rpc`, `cli`, `sdk`, `config`, `parameters`,
  `types`, `errors`, `spec`, `endpoint`
- `guide` — `guide`, `tutorial`, `quickstart`, `how-to`, `learn`, `getting-started`,
  `concept`, `example`, `walkthrough`
- otherwise `mixed`

Set-level: landing share ≥ 0.5 → `landing` (hard fail, debt); ≥ 0.2 → `mixed`
(warn); else the majority of the non-landing pages.

`validate` fails when: any mandatory field is empty, `licence` is UNKNOWN, `quality` is
`landing`, or `landing_ratio ≥ 0.40`.

## 3. What to exclude

Never ingest: changelogs/release notes, blog/news indexes, pricing/careers/legal pages,
i18n duplicates (keep `en`/default locale only), search and tag listings, generated API
dumps of a single page, or any set whose landing share would exceed 0.5. Marketing
landing pages poison retrieval: they rank high on brand queries and contain no facts.

## 4. Chunking

- Chunk **by heading** (`h2`/`h3`), never by fixed character count.
- One chunk = one section, with its heading path prepended to the text.
- Tables and code fences stay whole inside the chunk that introduced them.
- Target 300–1200 tokens; oversized sections split at the next heading of the same
  level, never mid-code-block.
- Every chunk carries: `library_id`, `library_name`, `source_url`, `file_path` (page
  URL), `title` (section heading), `license`, `chunk_index`.
- **Licence travels with the chunk** — no chunk without `license`; `publishable` is
  derived from it, not from the registry's opinion.

## 5. Licence handling

- Record the licence of the **upstream repository/site**, not the scraper.
- `UNKNOWN` blocks the set: it may stay in Qdrant for internal search, but it cannot be
  marked publishable and must be re-checked before any external use.
- Copyleft (GPL/AGPL) sets: internal-only, `publishable=false`.
- Whitepapers/PDFs: cite author + year in `display_name`; licence is the publisher's
  terms (often `NOASSERTION` → treat as unpublishable).

## 6. Refresh cadence

| class | cadence | trigger |
|---|---|---|
| vendor API references (`docs:*` SDK/RPC) | 30 days | provider changelog, 4xx/404 spike |
| protocol docs (Aave, Uniswap, OpenZeppelin) | 90 days | major version release |
| security/incident corpora (rekt, SlowMist, BlockSec) | 7 days | new incident |
| whitepapers | never (immutable) | new revision only |
| our own docs (`internal/*`) | on merge | repo push |

A refresh is: re-crawl → rebuild with the same `library_id` → `refresh-status` →
`validate` must stay rc=0 → note the new `fetched_at`. Sets untouched past 2× their
cadence are reported as stale by `refresh-status`.

## 7. Query

`docs_query(libraryId, query)` in ContextCrypto, or `docs_registry.py search <q>` to find
the `library_id` first. Exact identifiers go to zoekt, not to the docs corpus.
