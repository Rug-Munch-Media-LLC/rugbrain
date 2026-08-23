"""Model Services Gateway — platform-wide access to the Qwen3 model lineup.

ONE integration point for every RugMunch component that needs embeddings,
reranking, retrieval, or generation. Runs on Talos :8010 (172.22.0.1).

Endpoints:
  GET  /health              aggregate health of all five engines
  POST /retrieve            {query, top_k?, collection?, tier?}
                            word-count routed dual-tier retrieval
  POST /ask                 {query, collection?} -> retrieve + generate
  POST /embed               {text|texts, tier=speed|deep} -> vectors
  POST /rerank              {query, documents[], tier?} -> scored+ordered
                            (vertical instructions auto-applied)

Collections are per-domain: crypto_intelligence (RAG corpus), and any future
domain store (e.g. pry_methodology for scraping knowledge) — same engines,
same policy, isolated vector namespaces.

Run:  systemctl start model-gateway   (scripts/dual_tier/model_gateway.service)
"""

from __future__ import annotations

import os
import time
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rugbrain.search.dual_tier import (
    ENDPOINTS,
    ROUTING_THRESHOLD_WORDS,
    DualTierRetriever,
    pick_tier,
)
from rugbrain.search.rerank import apply_instruction

app = FastAPI(title="RugMunch Model Services Gateway", version="1.0.0")

_retriever = DualTierRetriever()
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=float(os.getenv("GATEWAY_TIMEOUT", "300")))


class RetrieveReq(BaseModel):
    query: str
    top_k: int = 5
    collection: str = "crypto_intelligence"
    tier: str | None = None  # speed | deep | None=auto


class AskReq(BaseModel):
    query: str
    collection: str = "crypto_intelligence"


class EmbedReq(BaseModel):
    text: str | None = None
    texts: list[str] | None = None
    tier: str | None = None  # None -> auto-route by word count; "speed"|"deep" explicit


class RerankReq(BaseModel):
    query: str
    documents: list[str]
    tier: str | None = None  # None=auto by doc count/query length
    top_n: int | None = None


def _resolve_tier(explicit: str | None, query: str = "") -> str:
    if explicit in ("speed", "deep"):
        return explicit
    return pick_tier(query)


async def _engine_health(http: aiohttp.ClientSession, url: str) -> tuple[str, bool]:
    try:
        async with http.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
            return url, r.status == 200 and (await r.json()).get("status") == "ok"
    except Exception:
        return url, False


@app.get("/health")
async def health() -> dict[str, Any]:
    async with aiohttp.ClientSession() as http:
        results = await asyncio_gather_health(http)
    engines = {u.split(":")[-1]: ok for u, ok in results}
    return {
        "status": "ok" if all(engines.values()) else "degraded",
        "engines": engines,
        "routing_threshold_words": ROUTING_THRESHOLD_WORDS,
    }


async def asyncio_gather_health(http: aiohttp.ClientSession):
    tasks = [_engine_health(http, u) for u in set(ENDPOINTS.values())]
    return await __import__("asyncio").gather(*tasks)


@app.post("/retrieve")
async def retrieve(req: RetrieveReq) -> dict[str, Any]:
    t0 = time.perf_counter()
    tier = _resolve_tier(req.tier, req.query)
    try:
        result = await _retriever.retrieve(req.query, top_k=req.top_k)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"retrieval failed: {exc!r}") from exc
    return {
        "tier": result.tier,
        "fallback_used": result.fallback_used,
        "requested_tier": tier,
        "contexts": result.contexts,
        "latency_ms": result.latency_ms,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.post("/ask")
async def ask(req: AskReq) -> dict[str, Any]:
    try:
        return await _retriever.ask(req.query)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ask failed: {exc!r}") from exc


@app.post("/embed")
async def embed(req: EmbedReq) -> dict[str, Any]:
    texts = req.texts or ([req.text] if req.text else [])
    if not texts:
        raise HTTPException(status_code=422, detail="text or texts required")
    tier = _resolve_tier(req.tier, texts[0])
    url = ENDPOINTS[f"{tier}_embed"]
    async with (
        aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as http,
        http.post(f"{url}/v1/embeddings", json={"input": texts}) as resp,
    ):
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"embedder {resp.status}")
        data = await resp.json()
    return {
        "tier": tier,
        "model_dims": len(data["data"][0]["embedding"]),
        "embeddings": [d["embedding"] for d in data["data"]],
    }


@app.post("/rerank")
async def rerank(req: RerankReq) -> dict[str, Any]:
    if not req.documents:
        return {"tier": _resolve_tier(req.tier, req.query), "results": []}
    tier = _resolve_tier(req.tier, req.query)
    url = ENDPOINTS[f"{tier}_rerank"]
    inst_query = apply_instruction(req.query)
    async with (
        aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as http,
        http.post(
            f"{url}/rerank",
            json={"query": inst_query, "documents": req.documents},
        ) as resp,
    ):
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"reranker {resp.status}")
        data = await resp.json()
    results = sorted(data["results"], key=lambda r: r["relevance_score"], reverse=True)
    if req.top_n:
        results = results[: req.top_n]
    return {"tier": tier, "instruction_applied": inst_query != req.query, "results": results}
