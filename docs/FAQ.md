# RugBrain — FAQ

**What is RugBrain?** A standalone crypto-intelligence RAG: hybrid search (dense + BM25/SPLADE), Infinity embeddings, cross-encoder reranking, PII scrubbing, multi-hop query decomposition, GraphRAG.

**Which models does it use?** Local llama.cpp services by default (qwen3 embedding + reranker tiers); OpenRouter fallbacks configurable via env.

**What data does it index?** Your corpus of choice — docs, threat intel, market notes. Ingestion scripts live under `scripts/`.

**How do I evaluate retrieval quality?** `docs/eval-baseline.md` documents the harness and baseline numbers.

**Does it need GPU?** No — CPU llama.cpp tiers work; deep tier uses the 4B models.

**How is PII handled?** Scrubbing runs pre-index and pre-response; see docs/DUAL_TIER_RAG.md.
