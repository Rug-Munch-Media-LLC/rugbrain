"""Model Services Gateway — platform-wide access to the Qwen3 model lineup.

ONE integration point for every RugMunch component that needs embeddings,
reranking, retrieval, or generation. Runs on Talos :8010 (127.0.0.1).

Endpoints:
  GET  /health              aggregate health of all five engines
  POST /retrieve            {query, top_k?, collection?, tier?}
                            word-count routed dual-tier retrieval
  POST /ask                 {query, collection?} -> retrieve + generate
  POST /embed               {text|texts, tier?} -> vectors
                            (None tier -> canonical deep lane; "speed" =
                            LEGACY 0.6b read lane, local-only; DeepInfra
                            Qwen3-Embedding-8B @4096 deep fallback —
                            index-compatible)
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
    ROUTING_THRESHOLD_WORDS,
    DualTierRetriever,
    pick_tier,
)
from rugbrain.search.rerank import apply_instruction
from scripts.dual_tier.engine_endpoints import ENDPOINTS

app = FastAPI(title="RugMunch Model Services Gateway", version="1.0.0")

_retriever = DualTierRetriever()
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=float(os.getenv("GATEWAY_TIMEOUT", "300")))

# --- Cloud fallback (2026-08-29 embed consolidation) ------------------------
# CANONICAL DEEP LANE: DeepInfra Qwen3-Embedding-8B - the exact same
# model the local :8004 serves (4096-dim, SAME vector space), so DeepInfra
# responses ARE index-compatible with locally built collections. The old
# OpenRouter two-account doctrine (liquid 350m speed / qwen3-embedding-8b
# deep) is retired: qwen3-embedding-8b is consumer-chat space, never
# index-compatible with the 8B corpus.
# SPEED LABEL: kept for API compatibility only — the 0.6b tier is
# retired (2026-09-14) and ENDPOINTS["speed_*"] resolve to the canonical 8B
# lanes. Set DEEPINFRA_API_KEY (env) or DEEPINFRA_KEY_FILE for the cloud rung.
DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
# 2026-08-29: defaulted to 4B, which is why this gateway kept the fleet on the
# retired lane regardless of what the rest of the repo said. Canonical now.
DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
DEEPINFRA_KEY_FILE = os.getenv("DEEPINFRA_KEY_FILE", "/run/secrets/deepinfra_api_key")


def _deepinfra_key() -> str:
    """DeepInfra API key: env var first, secrets-file fallback (never raises)."""
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(DEEPINFRA_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


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
    """DeepInfra lane — DEEP tier ONLY.

    Same model as the local deep lane ⇒ index-compatible. Speed requests
    resolve to the deep lane (0.6b retired 2026-09-14) so the same cloud
    rung applies; cross-model cloud fallback may never stand in for a tier
    (TOP-100 #25).
    """
    if tier not in ("deep", "speed"):
        return None  # speed resolves to the deep lane (0.6b retired 2026-09-14)
    key = _deepinfra_key()
    if not key:
        return None
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as http,
            http.post(
                DEEPINFRA_EMBED_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": DEEPINFRA_EMBED_MODEL, "input": texts},
            ) as resp,
        ):
            if resp.status != 200:
                return None
            data = await resp.json()
        vecs = normalize_embeddings(data)
        return len(vecs[0]), vecs, DEEPINFRA_EMBED_MODEL
    except Exception:
        return None


# --- Universal embedding-output interpreter ---------------------------------
# The platform spans 6+ engines (local 8B/0.6B quantized, chat 8B, cloud liquid
# 350m, cloud qwen3-8b, future hosts). Each emits OpenAI-ish JSON with drift:
# optional `index`, float lists vs base64-packed floats. Callers must consume
# ANY engine's output without knowing the source; unknown shapes fail loud.
ENGINE_REGISTRY: dict[str, dict[str, Any]] = {
    # key = f"{tier}_{served_by}"; dims are canonical for compatibility checks.
    # 2026-08-29 — 4B ELIMINATED. This registry hardcoded
    # Qwen3-Embedding-4B @2560 for every lane AND line 60 defaulted
    # DEEPINFRA_EMBED_MODEL to 4B, so this gateway — the service every embed
    # request routes through on :8090/:8010 — was the single largest reason
    # the fleet was still on 4B after the rest of the code had moved to 8B.
    #
    # deep_local is the llama.cpp :8004 endpoint. It is currently DOWN (the
    # service is masked), so the local lanes cannot serve today; they are kept
    # with the canonical dims so the registry stays internally consistent and a
    # future 8B local server is a config change, not a code change.
    #
    # speed_cloud is GONE (0.6b retired 2026-08-29) and the speed label
    # resolves to the deep lane (2026-09-14).
    "deep_local": {"dims": 4096, "model": "qwen3-embedding-8b"},
    "speed_local": {"dims": 4096, "model": "qwen3-embedding-8b"},  # compat label; deep lane
    "deep_cloud": {"dims": 4096, "model": "Qwen/Qwen3-Embedding-8B"},  # DeepInfra
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
    tier: str | None = None  # None -> deep (canonical, 2026-08-29);
    # "speed" = compat label — resolves to the deep 8B lane (2026-09-14)


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
    # 2026-08-29 consolidation: /embed defaults to the CANONICAL deep lane
    # (qwen3-embedding-8b @4096, DeepInfra — index-compatible).
    # Explicit tier="speed" is honored for API compat and resolves to
    # the canonical deep lane (0.6b retired 2026-09-14).
    tier = req.tier if req.tier in ("speed", "deep") else "deep"
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
                "index_compatible": True,  # same model as local deep lane
                "warning": (
                    f"{tier} breaker open (repeated local failures); served by "
                    f"DeepInfra {model_id} — same model as the local deep lane "
                    f"(index-compatible)"
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
        out = {
            "tier": tier,
            "served_by": "local",
            "model": ENGINE_REGISTRY.get(f"{tier}_local", {}).get("model"),
            "model_dims": len(vecs[0]),
            "embeddings": vecs,
        }
        if tier == "speed":
            out["legacy"] = True  # compat label; served by the deep 8B lane
        return out
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
                # DeepInfra serves the SAME qwen3-embedding-4b as the local
                # deep lane — index-compatible (unlike the retired OpenRouter
                # qwen3-8b lane).
                "index_compatible": True,
                "warning": (
                    f"local {tier} embedder unavailable ({exc!r}); served by "
                    f"DeepInfra {model_id} — same model as local deep lane "
                    f"(index-compatible)"
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
    # Rerank: Qwen3-Reranker-8B, DeepInfra primary, HF ZeroGPU in the free
    # windows. The "stays local, never cloud" line below is RETIRED — the local
    # reranker at :8003 is masked and the hosted 8B reranker is verified live
    # (200 OK, 85ms, $0.000016/call).
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
