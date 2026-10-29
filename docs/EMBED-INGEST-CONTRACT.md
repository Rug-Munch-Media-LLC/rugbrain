# Embed lane contract for ingests

Every ingest resolves ONE embedding lane before it writes anything.

## Lane order

1. `siliconflow` — `Qwen/Qwen3-Embedding-8B`, 4096-d, `https://api.siliconflow.com/v1/embeddings`
   (corpus-valid; the only lane an ingest may default to).
2. `deepinfra` / `openrouter` — same model, 4096-d, hosted.
3. `local` (:8024) — 4096-d but slow and key-gated; **never a silent fallback**.

`EMBED_PROVIDER` selects the lane (default `siliconflow`). A non-corpus-valid
lane (1024/2560-d, e.g. :8004) is refused, never downsized into.

## File-first key

The key comes from the FILE first, env only as a fallback:

    /srv/secrets/siliconflow_api_key      (file wins)
    SILICONFLOW_API_KEY                   (env fallback)

A stale/empty env var must never override a good file. The key value is never logged.

## Abort condition

Before the first upsert the ingest calls `resolve_ingest_lane()`
(`scripts/ingest_lane.py`), which probes the lane and requires `dim == 4096`.
No probe-confirmed 4096-d lane => typed abort, zero writes, no local fallback.

## Exact error text

    EmbedLaneUnavailable: ingest aborted: embed lane siliconflow-sl unusable
    (reason=<probe error|dim_N> want_dim=4096 url=https://api.siliconflow.com/v1/embeddings)

Missing key:

    EmbedLaneUnavailable: ingest aborted: embed lane siliconflow-sl unusable
    (reason=missing_key key_file=/srv/secrets/siliconflow_api_key env=SILICONFLOW_API_KEY)

History: the local :8024 lane returned `HTTP 401 Invalid API Key` four times while
ingests exited 0 with `upserted=0`. The lane is now proven, named, and loud.
