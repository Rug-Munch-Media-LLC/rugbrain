"""Boundary protocols for RugBrain.

These protocols define the seams between the pure RAG engine and the
outside world. The private monorepo implements the real providers
(scanner, balances, knowledge graph, crawler, LLM); the OSS package
ships stubs (``rugbrain/stubs/``) and fails open when a provider is
absent.

Every protocol is runtime-checkable and async-first, matching the
portable ``app/rag`` modules they replace (see MIGRATION.md §2).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# CacheBackend is defined next to its implementations (in-memory + Redis)
# in ``rugbrain/storage/cache.py`` so the protocol matches the real call
# surface the ported modules use. Re-exported here as the canonical home.
from rugbrain.storage.cache import CacheBackend  # noqa: F401

__all__ = [
    "BalancesClient",
    "CacheBackend",
    "CrawlerProvider",
    "EmbeddingProvider",
    "KGProvider",
    "LLMRouter",
    "NewsProvider",
    "Reranker",
    "ScannerClient",
]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embeds text into vectors. Replaces the Infinity client."""

    async def embed(self, texts: list[str], *, collection: str = "") -> list[list[float]]: ...


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder rerank. Replaces app/rag/rerank.py's /rerank client."""

    async def rerank(self, query: str, docs: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]: ...


@runtime_checkable
class ScannerClient(Protocol):
    """Token/wallet scanner. Replaces app.domains.scanner.scan_token (app/rag/tools.py)."""

    async def scan_token(self, query: str, chain: str = "ethereum") -> dict[str, Any]: ...


@runtime_checkable
class BalancesClient(Protocol):
    """Live wallet balances. Replaces app.services.web3_chat.balances (app/rag/tools.py)."""

    async def get_wallet_balance(self, query: str, chain: str | None = None) -> dict[str, Any]: ...


@runtime_checkable
class KGProvider(Protocol):
    """Entity-aware knowledge-graph enrichment.

    Replaces the lazy ``app.knowledge_graph.graph`` import in the private
    ``app/rag/retrieval.py`` (graph-enhanced retrieval step). Optional —
    default ``None``, fail-open. The surface matches the two methods the
    portable code actually calls:

    - ``extract_entities(query)`` — entity names mentioned in the query
    - ``search_related(entities, limit)`` — graph-neighbor documents

    Note: the scaffold originally described ``entity_boost(query, tokens)``;
    the real call site uses extract/search, so the protocol was revised to
    the actual surface during porting (see PORTING-NOTES.md).
    """

    async def extract_entities(self, query: str) -> list[str]: ...

    async def search_related(self, entities: list[str], limit: int = 5) -> list[dict[str, Any]]: ...


@runtime_checkable
class CrawlerProvider(Protocol):
    """Web page extraction. Replaces app.services.pryscraper_client.PryscraperClient
    in app/rag/pryscraper_ingest.py."""

    async def extract(self, url: str) -> str: ...


@runtime_checkable
class NewsProvider(Protocol):
    """News search for the tools layer. Replaces the private news DataBus sources."""

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...


@runtime_checkable
class LLMRouter(Protocol):
    """LLM synthesis. Replaces app.llm_config.generate_content (app/rag/graph.py)."""

    async def generate(self, prompt: str, **kwargs: Any) -> str: ...
