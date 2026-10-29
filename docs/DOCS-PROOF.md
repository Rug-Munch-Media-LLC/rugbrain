# DOCS-PROOF — Chainlink re-proof + social docs ingest (worktree rb-reproof41)

Run: 2026-10-25, worktree `/srv/work/wt/rb-reproof41` @ `ec2cc8d` (forgejo/main).
Env: `/srv/work/repos/rugbrain/.env.rag`, collection `rag_documents_8b` (4096-d).

## Verdict summary

| Question | Cited page | Verdict |
|---|---|---|
| Chainlink (a) how `minAnswer`/`maxAnswer` mask a depeg | — | **BLOCKED** (no ingest) |
| Chainlink (b) what a consumer must configure to detect it | — | **BLOCKED** (no ingest) |
| Farcaster signer/key handling | — | **BLOCKED** (write path) |
| AT Protocol repo/commit semantics | — | **BLOCKED** (write path) |
| Mastodon rate-limit + webhook behaviour | — | **BLOCKED** (write path) |

Nothing below is a retrieval-quality result: the documents never reached Qdrant, so
ranking/chunking/page-selection could not be evaluated at all.

## Evidence (measured, not inferred)

1. **Chainlink fetch never started — GitHub tree API rate-limited.**
   `repo_docs.collect` → `list_repo_files` → `github_tree_failed repo=smartcontractkit/documentation
   ref=5f4f60df974d status=403` → `source_fetch_failed reason=github_rate_limited`.
   Stats: `listed=0 pages=0 files=0 licence="" publishable=False by_page_kind={}`.
   The reference-first cap (`_page_priority` in `src/rugbrain/ingest_sources/repo_docs.py:37-42`)
   was therefore **never exercised** — the 200-page budget, and whether it reaches
   `src/content/docs/data-feeds/**`, is **unproven** in this run.

2. **Social pages fetched, but the write path fails for every source.**
   `devdocs.collect(sites=SOCIAL_SITES)` fetched the 4 registered sets
   (farcaster_hub 3, atproto 8, mastodon_docs 5, lens_core 1 = 17 declared paths;
   `fetched=14` documents logged by `_store_docs`), then `_store_docs` reported
   `embedded=0 errors=305 blocked_collections=['rag_documents_8b']`.

   Root cause is a signature mismatch inside the store path, reproduced with both
   plausible client types:
   - `_store_docs(store, httpx.AsyncClient, …)` → `rugbrain/qdrant_store/write_guard.py:34
     slot_accepts → client.get_collection` → `AttributeError: 'AsyncClient' object has no
     attribute 'get_collection'`.
   - `_store_docs(store, AsyncQdrantClient, …)` → `'AsyncQdrantClient' object has no
     attribute 'post'`.
   `inspect.signature(ingest_all_sources._store_docs)` is
   `(store, client: httpx.AsyncClient, source_name, default_collection, docs, dry_run)`,
   so the httpx form is the declared contract — meaning `QdrantVectorStore`
   (as constructed here, `QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))`)
   is handing an HTTP client into a Qdrant-client-shaped guard.

3. **Embedding lane.** First attempt (no env file) failed with
   `embed_lane_parked lane=local status=permanent` + `local:http_401` at
   `http://127.0.0.1:8004/v1/embeddings`; sourcing `.env.rag` cleared the auth failure,
   so the lane is not the remaining blocker.

## Reproduction

```bash
sqlite3 /dev/null 2>/dev/null; cd /srv/work/wt/rb-reproof41
set +u; set -a; . /srv/work/repos/rugbrain/.env.rag; set +a; set -u
export PYTHONPATH=$PWD/src:$PWD
/srv/work/repos/rugbrain/.venv/bin/python /tmp/reproof_docs4.py
```

## Next actions (owner: lead)

1. **Code fix (root cause, not workaround):** make `qdrant_store.insert`'s write guard
   accept the same client type `_store_docs` passes (or give `QdrantVectorStore` the
   client the guard expects). This blocks *every* `ingest_all_sources` source, not only docs.
2. **GitHub token:** export `GITHUB_TOKEN` (tree listing is unauthenticated → 60 req/h; the
   chainlink tree walk exhausted it). Re-run step 1, then re-measure `by_page_kind` and
   whether the top-200 window contains `src/content/docs/data-feeds/**`.
3. Only after both: re-ask the two mechanism questions with the `source=chainlink_docs`
   filter and record the cited `path` for each hit.
