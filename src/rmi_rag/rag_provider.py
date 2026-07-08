"""
DataBus RAG Provider - wire the world-class RAG engine into DataBus.
"""

import logging

logger = logging.getLogger("databus.rag_provider")


async def _rag_search_provider(**kwargs) -> dict | None:
    """DataBus provider for hybrid RAG search across all collections."""
    from dotenv import load_dotenv

    load_dotenv("/root/backend/.env", override=True)
    query = kwargs.get("query", kwargs.get("q", ""))
    collections = kwargs.get("collections", [])
    top_k = int(kwargs.get("limit", 10))
    enrich = kwargs.get("enrich", False)

    if not query:
        return {
            "error": "query required",
            "collections": [
                "rmi_news",
                "rmi_scams",
                "rmi_research",
                "rmi_entities",
                "rmi_security",
            ],
        }

    try:
        from app.domains.databus.rag_engine import ai_enrich, hybrid_search  # external dependency

        if isinstance(collections, str):
            collections = [c.strip() for c in collections.split(",") if c.strip()]
        if not collections:
            collections = ["rmi_news"]

        results = hybrid_search(query, collections, top_k)

        if enrich and results.get("results"):
            results["ai_summary"] = ai_enrich(query, results["results"])

        return {
            "query": query,
            "results": results.get("results", []),
            "ai_summary": results.get("ai_summary", ""),
            "collections_searched": collections,
            "total_collections": 5,
            "source": "RMI RAG Engine (Qdrant + Ollama embeddings + Redis cache)",
            "model": "nomic-embed-text (768d)",
        }
    except Exception as e:
        return {"error": str(e), "source": "RMI RAG Engine"}
