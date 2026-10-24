# Content pipeline — one order, every source

Canonical flow. There is no per-channel path and no privileged source: every
writer produces documents that enter the **same** candidate pool, and the same
story arriving through several channels is published **once**.

```
source → ingest → normalize → dedupe (hash + cross-channel)
       → embed (4096) → rerank → candidate selection → channel/feed
```

Owner of each stage is a file, not a service. If a stage moves, this table
moves with it.

## 1. Source → ingest

| Source family | Collector (file) | Collection written | Timestamp field | Link field |
|---|---|---|---|---|
| Telegram — accountless preview sweep | `src/rugbrain/ingest_sources/telegram_web.py` | `news_v2` (news), `social_intel` (social chatter), `threat_intel_v2` (critical/high) | `metadata.ts` (ISO) | `metadata.url` |
| Telegram — userbot / alt ingest | `src/rugbrain/ingest_sources/telegram_userbot.py`, `alt_ingest.py` (`alt_ingest_store.py`, `alt_ingest_channels.py`) | `social_intel` | `metadata.ts` (ISO) | `metadata.url` |
| Telegram — scam channel discovery | `src/rugbrain/ingest_sources/scam_channels_pinned.py`, `discover_scam_channels.py` | `scam_intel_v2` (reference corpus) | `metadata.ts` | `metadata.url` |
| X / Twitter | `src/rugbrain/ingest_sources/x_intel/` (`pipeline.py` → `storage.py`, collection from `config.RAG_COLLECTION`) | `news_v2`, `metadata.category = x_social` | `metadata.ts` (**tweet dialect**) | `metadata.url` |
| RSS / research feeds | `src/rugbrain/ingest_sources/feeds.py` (`rss_item_to_doc`), parsers `rss_parser.py`, `rss_feed.py` | `research_v2`, `regulation_enforcement` | `metadata.published` (ISO) | `metadata.source_url` |
| Firehose / backend news | `src/rugbrain/firehose/direct_sources.py` (`fetch_news_direct`) → `firehose/news.py` → `firehose/engine.py` | `news_articles` | `metadata.published` (RFC 2822) | `metadata.url` |

`threat_intel_v2`, `scam_intel_v2`, `rag_documents`, `known_scams` are
**reference corpora**, not story sources: undated blacklists, phishing feeds and
historical incident write-ups. They are excluded from candidate selection on
purpose — probing them re-publishes old news (stale-news incident, 2026-08-26).

## 2. Normalize

`src/rugbrain/content_story.py` — `normalise()` flattens any of the payload
shapes above into one story dict, and `src/rugbrain/content_time.py` —
`to_iso8601()` converts the timestamp dialect to ISO-8601.

This stage is load-bearing, not cosmetic. The publisher's recency gate parses
`ts` with `datetime.fromisoformat` and **fails closed**, so:

- X posts stored in the tweet dialect (`Wed Sep 16 18:18:29 +0000 2026`) were
  unpublishable — 1,821 of them sat in `news_v2_8b` and none could post;
- RSS research carried `published`, never `ts`, so it had no timestamp at all.

A story whose timestamp will not normalise is dropped at selection, so it never
occupies a rerank slot.

## 3. Dedupe — two stages, different jobs

| Stage | File | What it collapses |
|---|---|---|
| Ingest-side, within a batch | `src/rugbrain/ingest_sources/ingest_dedupe.py` (`normalize_for_dedupe`, `shingles`, `jaccard`, `cluster_duplicates`) | Exact normalised-text hash first, then token-shingle Jaccard ≥ 0.62 — one wire story pasted into five Telegram channels |
| Candidate-side, across channels | `src/rugbrain/content_dedupe.py` (`collapse`) | The same story from **different source families** — Telegram + X + RSS. Reuses the ingest similarity measure; keeps one representative and records `also_seen_in` / `corroboration` |

The previous candidate key was a 120-character content prefix
(`content_story.dedupe_key`), which cannot match two channels that pasted the
same wire text with their own link and emoji — so the same story could post
twice in one cycle. `dedupe_key` is now only the cheap exact identity
(tweet id, else body prefix); cross-channel syndication is `collapse`'s job.

## 4. Embed (4096)

`src/rugbrain/embeddings.py` — one shared embedder, canonical lane
`Qwen3-Embedding-8B`, **4096-d**, cosine. Collection names resolve to their
`_8b` twin by `src/rugbrain/qdrant_store/lane_resolver.py`
(`news_v2` → `news_v2_8b`), and `namespace.unified_route` composes the lane with
the consolidated-read window. Every collection in the pool is 4096-d (verified
live 2026-10-24):

| Collection | Points | Dim |
|---|---|---|
| `news_v2_8b` | 299,067 | 4096 (`dense`) |
| `social_intel_8b` | 17,929 | 4096 |
| `research_v2_8b` | 164 | 4096 |
| `news_articles_8b` | 2,451 | 4096 |
| `rag_documents_8b` | 1,136,347 | 4096 (`dense`) |
| `scam_intel_v2_8b` | 119,269 | 4096 (`dense`) |
| `threat_intel_v2_8b` | 195,320 | 4096 (`dense`) |

A write at a base name and a read at the twin are the same corpus; a mismatch
is refused by the dimension guard rather than forking a second corpus.

## 5. Rerank

`src/rugbrain/content_rerank.py` — one standing editorial intent
(`EDITORIAL_INTENT`), cross-encoded by Qwen3-Reranker-8B: local `:8003`
(`llama-rerank.service`) first, then the hosted chain
(`search.rerank.rerank_chunks`: siliconflow → openrouter → deepinfra). Retrieval
runs at a deliberately low floor (`MIN_SIMILARITY = 0.15`) because the
cross-encoder is the real triage; `MIN_RELEVANCE = 0.15` then drops off-intent
candidates so padding never fills the quota. Every story carries
`rerank_method` / `rerank_reason`; a reranker outage degrades to unreranked
stories rather than silence.

## 6. Candidate selection

`src/rugbrain/content_candidates.py` — `recent_stories()`:

1. one vector probe per collection in `CANDIDATE_COLLECTIONS`
   (`news_v2`, `social_intel`, `research_v2`, `news_articles`);
2. plus one `x_social`-filtered probe on `news_v2` only (X posts are a small
   family in a 299k-point collection and an unfiltered search never admits one);
3. `content_dedupe.collapse` over the top 200 by importance;
4. drop undated stories;
5. rerank → best `limit`.

There is no WatcherGuru precedence and no source allowlist here: WatcherGuru is
one Telegram channel inside `news_v2`, competing on the same ranking as every
other channel, X account and feed.

## 7. Channel / feed

`src/rugbrain/content_engine.py` — `generate_channel_drafts()` turns the ranked
pool into per-channel drafts (`main`, `alerts`, `news`, `scans`), with the
writer pass and the fact-check gate (`factcheck_gate.py`). `scans` comes from
`select_scans()` over `scanner_feedback`, not from the story pool. The
publisher posts to Telegram and mirrors `main` to the site feed.

## Invariants

1. One pool. A new source is added by writing to a declared collection and
   listing it in `CANDIDATE_COLLECTIONS` — never by adding a channel branch.
2. Every candidate has an ISO-8601 `ts` or it does not reach rerank.
3. Cross-channel duplicates collapse to one representative; corroboration is
   kept as metadata, never as extra posts.
4. No collection in the pool may be undated. Undated knowledge bases belong in
   retrieval, not in publishing.

## Verifying it

```bash
pytest tests/test_content_candidates_pool.py -q          # pool, dedupe, ts
pytest tests/test_content_engine.py tests/test_content_rerank_lanes.py -q
python3 scripts/check-structure.py --repo .              # 200-line cap
```

Live probe (counts and dims above) is a plain Qdrant REST read against
`127.0.0.1:6334`; re-run it after any collection rename.
