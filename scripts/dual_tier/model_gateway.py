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

# --- Cloud fallback (two-account doctrine, 2026-08-23) ----------------------
# LOCAL CANON: deep = Qwen3-Embedding-4B :8004 (2560-dim), speed = 0.6B :8007.
# Cloud lanes engage ONLY when local fails/breaker opens; responses are labeled
# degraded and are NOT index-compatible with locally built collections.
#   speed lane -> liquid/lfm-2.5-embedding-350m:free  via OR MAIN (free)
#   deep lane  -> qwen/qwen3-embedding-8b             via OR BACKUP (reserve acct)
CLOUD_EMBED_BASE = os.getenv("CLOUD_EMBED_BASE", "https://openrouter.ai/api/v1")
CLOUD_MAIN_KEY = os.getenv("CLOUD_EMBED_KEY_MAIN", "")
CLOUD_BACKUP_KEY = os.getenv("CLOUD_EMBED_KEY_BACKUP", "")
CLOUD_MODELS = {
    "speed": os.getenv("CLOUD_EMBED_SPEED_MODEL", "liquid/lfm-2.5-embedding-350m:free"),
    "deep": os.getenv("CLOUD_EMBED_DEEP_MODEL", "qwen/qwen3-embedding-8b"),
}
_CLOUD_KEYS = {"speed": CLOUD_MAIN_KEY, "deep": CLOUD_BACKUP_KEY}

_cloud_fails: dict[str, int] = {"deep": 0, "speed": 0}
_BREAKER_TRIP = int(os.getenv("GATEWAY_BREAKER_TRIP", "3"))
_BREAKER_COOLDOWN = float(os.getenv("GATEWAY_BREAKER_COOLDOWN", "120"))
_breaker_open_until: dict[str, float] = {"deep": 0.0, "speed": 0.0}


def _note_failure(tier: str) -> None:
    """Strain breaker: N consecutive local failures open a cooldown window."""
    _cloud_fails[tier] += 1
    if _cloud_fails[tier] >= _BREAKER_TRIP:
        _breaker_open_until[tier] = time.time() + _BREAKER_COOLDOWN


def _breaker_open(tier: str) -> bool:
    return time.time() < _breaker_open_until.get(tier, 0.0)


def _retry_after(tier: str) -> str:
    """Cooldown-aware Retry-For hint: remaining breaker window or short default."""
    if _breaker_open(tier):
        return str(int(_breaker_open_until[tier] - time.time()) + 1)
    return "15"


async def _cloud_embed(tier: str, texts: list[str]) -> tuple[int, list[list[float]], str] | None:
    key = _CLOUD_KEYS.get(tier, "")
    model = CLOUD_MODELS.get(tier, "")
    if not key or not model:
        return None
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as http,
            http.post(
                f"{CLOUD_EMBED_BASE}/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": texts},
            ) as resp,
        ):
            if resp.status != 200:
                return None
            data = await resp.json()
        vecs = normalize_embeddings(data)
        return len(vecs[0]), vecs, model
    except Exception:
        return None


# --- Universal embedding-output interpreter ---------------------------------
# The platform spans 6+ engines (local 4B/0.6B quantized, chat 8B, cloud liquid
# 350m, cloud qwen3-8b, future hosts). Each emits OpenAI-ish JSON with drift:
# optional `index`, float lists vs base64-packed floats. Callers must consume
# ANY engine's output without knowing the source; unknown shapes fail loud.
ENGINE_REGISTRY: dict[str, dict[str, Any]] = {
    # key = f"{tier}_{served_by}"; dims are canonical for compatibility checks
    "deep_local": {"dims": 2560, "model": "qwen3-embedding-4b"},
    "speed_local": {"dims": 1024, "model": "qwen3-embedding-0.6b"},
    "deep_cloud": {"dims": 4096, "model": "qwen/qwen3-embedding-8b"},
    "speed_cloud": {"dims": 1024, "model": "liquid/lfm-2.5-embedding-350m"},
}


def _vec_from_item(item: dict[str, Any]) -> list[float]:
    emb = item.get("embedding")
    if isinstance(emb, dict):  # nested wrapper variants
        emb = emb.get("embedding") or emb.get("vector") or emb.get("data")
    if isinstance(emb, str):  # base64-packed little-endian float32
        import base64
        import struct

        raw = base64.b64decode(emb)
        if len(raw) % 4:
            raise RuntimeError("base64 embedding length not float32-aligned")
        emb = list(struct.unpack(f"<{len(raw) // 4}f", raw))
    if not isinstance(emb, list) or not emb:
        raise RuntimeError("unrecognized embedding item shape")
    return [float(x) for x in emb]


def normalize_embeddings(data: dict[str, Any]) -> list[list[float]]:
    """Interpret any OpenAI-compatible embeddings payload into float vectors.

    Handles present/absent `index`, list or base64 embeddings, nested wrappers.
    Unknown shapes raise (fail-loud) — never guess.
    """
    items = data.get("data") if isinstance(data, dict) else None
    if not items:
        raise RuntimeError("embedder payload missing data[]")
    if all(isinstance(i, dict) and "index" in i for i in items):
        items = sorted(items, key=lambda x: x["index"])
    return [_vec_from_item(i) for i in items]


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
    if _breaker_open(tier):
        cloud = await _cloud_embed(tier, texts)
        if cloud is not None:
            dims, vecs, model_id = cloud
            return {
                "tier": tier,
                "served_by": "cloud",
                "cloud_model": model_id,
                "degraded": True,
                "warning": (
                    f"{tier} breaker open (repeated local failures); served by "
                    f"cloud {model_id} — do NOT index against local collections"
                ),
                "model_dims": dims,
                "embeddings": vecs,
            }
        # No cloud lane configured -> fall through and retry local anyway.
    try:
        async with (
            aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as http,
            http.post(f"{url}/v1/embeddings", json={"input": texts}) as resp,
        ):
            if resp.status != 200:
                raise RuntimeError(f"embedder {resp.status}")
            data = await resp.json()
        vecs = normalize_embeddings(data)
        _cloud_fails[tier] = 0  # healthy again — reset strain counter
        return {
            "tier": tier,
            "served_by": "local",
            "model": ENGINE_REGISTRY.get(f"{tier}_local", {}).get("model"),
            "model_dims": len(vecs[0]),
            "embeddings": vecs,
        }
    except Exception as exc:
        _note_failure(tier)
        cloud = await _cloud_embed(tier, texts)
        if cloud is not None:
            dims, vecs, model_id = cloud
            return {
                "tier": tier,
                "served_by": "cloud",
                "cloud_model": model_id,
                "degraded": True,
                # Cloud vectors are NOT index-compatible with locally built
                # collections (different model space). For fresh same-model
                # lanes / non-retrieval use only. Retrieval code must reject.
                "warning": (
                    f"local {tier} embedder unavailable ({exc!r}); served by cloud "
                    f"{model_id} — do NOT index against local collections"
                ),
                "model_dims": dims,
                "embeddings": vecs,
            }
        raise HTTPException(
            status_code=502,
            detail=f"embedder failed: {exc!r}",
            headers={"Retry-After": _retry_after(tier)},
        ) from exc


@app.post("/rerank")
async def rerank(req: RerankReq) -> dict[str, Any]:
    if not req.documents:
        return {"tier": _resolve_tier(req.tier, req.query), "results": []}
    tier = _resolve_tier(req.tier, req.query)
    inst_query = apply_instruction(req.query)
    body = {"query": inst_query, "documents": req.documents}
    # Rerank stays LOCAL per doctrine: 4B primary, 0.6B fallback. Never cloud.
    for attempt_tier in (tier, "speed" if tier == "deep" else "deep"):
        url = ENDPOINTS[f"{attempt_tier}_rerank"]
        try:
            async with (
                aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as http,
                http.post(f"{url}/rerank", json=body) as resp,
            ):
                if resp.status != 200:
                    raise RuntimeError(f"reranker {resp.status}")
                data = await resp.json()
            results = sorted(data["results"], key=lambda r: r["relevance_score"], reverse=True)
            if req.top_n:
                results = results[: req.top_n]
            out = {
                "tier": attempt_tier,
                "instruction_applied": inst_query != req.query,
                "results": results,
            }
            if attempt_tier != tier:
                out["degraded"] = True
                out["warning"] = f"{tier} reranker unavailable; served by {attempt_tier}"
            return out
        except Exception:
            continue
    raise HTTPException(
        status_code=502,
        detail="all local rerankers failed",
        headers={"Retry-After": _retry_after(tier)},
    )
