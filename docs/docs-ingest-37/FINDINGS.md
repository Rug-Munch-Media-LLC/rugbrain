# docs layer — measured cause of 0/4, 2026-10-25

## Measurement (production Qdrant `codebase_reference_v2_8b`)
- 60,000-point scroll: 256 distinct `docs:*` library_ids, **1,965 doc chunks total = 7.7 chunks/set**.
- Largest doc sets: polygon 29, openhands 28, crewai 27 — i.e. a whole documentation site in <30 chunks.
- `docs:pydantic` = 21 chunks, all with `page_url = https://docs.pydantic.dev/llms-full.txt`.
- `docs:nuxt` = 18 chunks, all `page_url = https://nuxt.com/llms-full.txt`.
- `docs:python-telegram-bot` = 4 chunks: `/en/v22.8/` (index) + `telegram.ext.conversationhandler.html` x2.
- `docs:sqlalchemy` = 1 chunk: `en/20/orm/quickstart.html` (landing).
- `docs:fastapi` = 0 chunks found in the 60k sample.

## Root cause (two stacked defects)
1. **Granularity**: `contextcrypto/scripts/docs_sync_discover.py` rung 1 is
   `llms-full.txt` — "the whole docs site as ONE markdown file (1 request)"
   (docstring, lines 4-10). So a whole site becomes one document, chunked, and every
   chunk carries the blob URL. No reference page, no per-page provenance, no
   `page_kind`. Answers therefore cite `llms-full.txt`, not a reference page.
2. **Retrievability**: question-form queries return `count: 0`.
   `docs_query(libraryId="fastapi", query="How do I declare a dependency with yield and cleanup in FastAPI?")`
   -> `{\"count\":0,\"results\":[]}` (same for pydantic/nuxt/qdrant). Keyword-shaped
   query `field_validator mode before pydantic v2` -> `count:3`. The hybrid path has
   no recall fallback when the NL query scores below threshold, so a Q&A proof
   phrased as a question gets 0/4 regardless of content.

## Fix contract (this dir)
`targets-reference.json` — 6 libraries, docs **source repos** only, reference+guide
globs, `llms-full.txt` excluded, <=300 pages/library, provenance
`{library,page_url,source_repo,licence,fetched_at,page_kind}`, `publishable` only
for MIT/Apache-2.0.

Licences verified via GitHub API 2026-10-25: fastapi MIT · pydantic MIT ·
sqlalchemy MIT · nuxt MIT · python-telegram-bot **GPL-3.0** (publishable=false) ·
qdrant Apache-2.0.
