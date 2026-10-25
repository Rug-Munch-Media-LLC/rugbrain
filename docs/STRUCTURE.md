# rugbrain module map and import rules

Measured on `main` (`1e593af`), `.venv` excluded. Numbers are file counts / line
counts under `src/rugbrain`; `scripts/` is counted separately because it is not
part of the installable library.

## Layers (dependency direction is downward only)

| Layer | Packages | What it owns |
| --- | --- | --- |
| Entrypoints | `main`, `app`, `endpoints`, `routes` (+`routes/search_pkg`), `mcp_bridge`, `rugbrain_mcp` | HTTP/MCP surface. Thin: validate, call a service, return. |
| Retrieval | `search` (35 files / 5.0k), `qdrant_store` (24/2.7k), `embeddings_pkg`, `crypto_embeddings_pkg`, `multi_embedder`, `splade_bm25`, `reranker` + `rerank_*`, `rrf_fusion`, `bundle_cluster`, `app_chunking`, `tree_sitter_chunking`, `solidity_chunker` | Hybrid retrieval: embed, fuse, rerank, chunk. |
| Ingestion | `ingest_sources` (147/21.1k; `x_intel` 20, `content_sources` 12), `firehose`, `realtime_alerts`, `monitors` (29/5.0k), `xsession`, `web_crawler`, `youtube_ingester`, `podcast_ingester`, `newsletter_ingester`, `ingest_smart`, `ingest_common` | External sources in, normalised items out. |
| Analysis | `eval` (34/4.9k), `missioneval`, `ta`, `zeroday`, `news_rating`, `news_ranking`, `sentiment_engine` + `monitors/social_sentiment`, `contract_analysis`, `scam_*`, `rug_events`, `liquidation_heatmap`, `fear_index`, `whale_flow`, `mev_detector`, `token_unlock_monitor`, `chain_parquet`, `hyperliquid_intel` | Scoring, classification, signal extraction. |
| Publishing | `alerts`, `realtime_alerts`, `digest`, `daily_rug*`, `journalism`, `content_*`, `formatting`, `citations`, `public_projection`, `dashboard` | Outbound alerts, digests, editorial output. |
| Platform | `guardrails`, `import_guard`, `observability`, `metrics`, `system_health`, `retention`, `compat`, `knowledge_boundary`, `token_budget`, `prompt_compression`, `permanence_*` | Cross-cutting concerns with no domain knowledge. |
| Knowledge (data) | `knowledge/crypto_expert`, `knowledge/contract_patterns` | Curated literals moved out of `scripts/`. Pure data. |

Measured edge weights (imports between top-level packages): `endpoints -> routes` 15,
`app -> routes` 5, `routes -> monitors` 12, `routes -> qdrant_store` 8,
`corpus -> qdrant_store` 7, `monitors -> alert_dispatcher` 12, `search -> compat` 16.

## Known structural debt (measured, not fixed here)

- Cyclic pairs: `endpoints <-> routes` (15/2), `embeddings <-> embeddings_pkg` (12/1),
  `reranker <-> rerank_compat`, `reranker <-> rerank_instructions`,
  `search_service <-> search`, `monitors <-> liquidation_heatmap`.
- God modules remaining in `src/`: `prompt_compression.py` 734, `bundle_cluster.py` 720,
  `splade_bm25.py` 719, `youtube_ingester.py` 671, `sentiment_engine.py` 669.
- `scripts/` is 169 files / 49k lines of application code that cannot be imported by the
  library. It is the largest outstanding extraction, one subsystem at a time.
- 237 `src` modules have no importers. Many are entrypoints or dynamically imported; a
  per-module audit is required before deleting anything (`vulture` alone is not proof).

## Import rules (enforced by `tests/test_package_boundaries.py`)

1. **The library never imports `scripts`.** `src/rugbrain/**` must not contain
   `import scripts…` or `from scripts… import`. Scripts may import the library.
2. **`rugbrain.knowledge` is a pure-data leaf.** It may import only its own siblings
   and the standard library — no `qdrant_store`, no `embeddings`, no HTTP client.

Both rules are checked by AST walk over the tree, so a single stray import fails
`make test`. To verify the checker still bites, drop a violating import into any
module and run `python3 tests/test_package_boundaries.py`; it must fail.
