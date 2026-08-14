"""RugBrain configuration (pydantic-settings, env-driven).

All knobs move verbatim from the private app/rag/config.py and the
settings read in app/rag/engine.py / qdrant_store.py / crawler.py /
embeddings.py / embedder.py / rerank.py. No secrets, no internal
hostnames — every endpoint defaults to localhost or is unset.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    # Vector store
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "rag_documents"
    store: str = "qdrant"  # qdrant | legacy (numpy+redis fallback)

    # Embedding + rerank backend (Infinity-compatible HTTP)
    infinity_url: str = "http://127.0.0.1:8001"
    embedder_primary: str = "infinity"  # infinity | bge-m3 | openrouter
    embedder_primary_model: str = "BAAI/bge-m3"
    # Deprecated legacy bge-m3 container HTTP service (nothing listens by default).
    bge_m3_url: str = ""

    # Embed cache
    embedder_cache_ttl: int = 86400
    embedder_cache_maxsize: int = 10000

    # Cache (optional; in-memory fallback). Enables Redis when set.
    redis_url: str = ""

    # Retrieval knobs (verbatim from app/rag/config.py)
    context_max_tokens: int = 4096
    ingestion_batch_size: int = 50
    ingestion_max_per_minute: int = 200
    chunk_size: int = 512
    chunk_overlap: int = 50
    min_chunk_chars: int = 32
    min_doc_length: int = 64
    spam_repetition_threshold: float = 0.3
    expansion_terms: int = 3
    hybrid_alpha: float = 0.7
    rrf_k: int = 60
    scheduler_interval: int = 300

    # Active collections (neutral defaults — replace with deployment config)
    collections: list[str] = ["docs"]

    # Extra crawl sources (JSON map) — replaces any private feed groups
    crawl_sources_extra: str = ""

    # Eval gates (CI)
    eval_hit_rate_min: float = 0.5
    eval_faithfulness_min: float = 0.25

    # Observability (off by default; no baked-in endpoint hostname)
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

__all__ = ["Settings", "get_settings", "settings"]
