"""RMI RAG — hybrid search, embeddings, chunking, agentic retrieval.

Public API surface.
"""
from rmi_rag import endpoints
from rmi_rag.cross_encoder import rerank
from rmi_rag.embeddings import (
    EMBED_MODEL_NAME,
    EMBED_MODEL_VERSION,
    EMBED_VERSION_STAMP,
    EMBEDDING_DIM,
    current_backend,
    get_embedding,
    get_embeddings,
)
from rmi_rag.incremental_indexer import DeltaTracker
from rmi_rag.parent_retriever import get_parent_retriever
from rmi_rag.qdrant_store import get_qdrant_store
from rmi_rag.query_rewriter import rewrite_query
from rmi_rag.search_service import search_multi_collection, search_similar, three_pillar_search

__all__ = [
    "EMBEDDING_DIM",
    "EMBED_MODEL_NAME",
    "EMBED_MODEL_VERSION",
    "EMBED_VERSION_STAMP",
    "AlertDispatcher",
    "DeltaTracker",
    "EmbeddingTrainingData",
    "WatchlistManager",
    "current_backend",
    "endpoints",
    "extract_pattern_from_url",
    "get_embedding",
    "get_embeddings",
    "get_fine_tuner",
    "get_parent_retriever",
    "get_qdrant_store",
    "rerank",
    "rewrite_query",
    "scam_check_router",
    "search_multi_collection",
    "search_similar",
    "three_pillar_search",
]
