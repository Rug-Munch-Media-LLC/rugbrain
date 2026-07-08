"""Embedding Router - thin wrapper around smart rate-limited dispatcher."""

from app.rate_limiter import get_dispatcher  # external dependency


async def get_router():
    return await get_dispatcher()


async def embed_texts(texts, task="default"):
    return await (await get_dispatcher()).embed(texts, task)


async def embed_single(text, task="default"):
    vecs, provider = await (await get_dispatcher()).embed([text], task)
    return vecs[0] if vecs else [], provider
