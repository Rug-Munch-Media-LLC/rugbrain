"""Ingest a public URL: fetch -> markdown -> chunk -> embed -> store.

Every stage is a port with a production default, so the pipeline is testable
without network, model or disk. Refusals come back as a typed envelope
(``ok=False`` + ``refusal`` + evidence) and never raise: a paywalled, robots-
disallowed, oversized or unembeddable page is a recorded decision.

Embedding uses the hosted Qwen3-Embedding-8B chain (4096-dim, see
``rugbrain.embedder``); set ``EMBED_PROVIDER_ORDER=siliconflow,deepinfra,
openrouter`` to make SiliconFlow the first rung.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from rugbrain.app_chunking import split_doc_into_chunks
from rugbrain.ingest.url_guards import MAX_BYTES, UA, check_url_allowed
from rugbrain.ingest.url_markdown import to_markdown

MIN_CHARS = 200
EMBED_DIM = 4096


@dataclass(frozen=True)
class UrlPayload:
    status: int
    content_type: str
    body: bytes
    robots_text: str | None = None


@dataclass
class UrlIngestReport:
    ok: bool
    url: str
    refusal: str | None = None
    status: int = 0
    bytes: int = 0
    markdown_chars: int = 0
    chunks: int = 0
    stored: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Fetch = Callable[[str], Awaitable[UrlPayload]]
Convert = Callable[[bytes, str, str], str]
Embed = Callable[[list[str]], Awaitable[list[list[float]]]]
Store = Callable[[list[dict[str, Any]]], Awaitable[int]]


async def _http_fetch(url: str) -> UrlPayload:
    """Default transport: robots.txt best-effort, then the page itself."""
    import httpx

    parsed = urlparse(url)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=30.0) as client:
        robots_text = None
        try:
            robots = await client.get(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
            robots_text = robots.text if robots.status_code == 200 else None
        except Exception:
            robots_text = None
        response = await client.get(url)
        return UrlPayload(response.status_code, response.headers.get("content-type", ""), response.content, robots_text)


async def _hosted_embed(texts: list[str]) -> list[list[float]]:
    from rugbrain.embedder import hosted_embed

    vectors = await hosted_embed(texts)
    if vectors is None:
        raise RuntimeError("all embedding rungs exhausted")
    return vectors


async def _jsonl_store(records: list[dict[str, Any]]) -> int:
    from rugbrain.ingest.url_store import store_records

    return await store_records(records)


async def ingest_url(
    url: str,
    *,
    fetch: Fetch | None = None,
    convert: Convert | None = None,
    embed: Embed | None = None,
    store: Store | None = None,
    max_bytes: int = MAX_BYTES,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Run the full pipeline for one URL and return a :class:`UrlIngestReport` dict."""
    stamp = fetched_at or datetime.now(UTC).isoformat(timespec="seconds")
    fetch = fetch or _http_fetch
    convert = convert or (lambda body, ctype, _url: to_markdown(body, ctype))
    embed = embed or _hosted_embed
    store = store or _jsonl_store
    try:
        payload = await fetch(url)
    except Exception as exc:
        return UrlIngestReport(False, url, "fetch_failed", detail=str(exc)[:200]).as_dict()
    verdict = check_url_allowed(
        url, payload.body, robots_text=payload.robots_text,
        content_type=payload.content_type, max_bytes=max_bytes,
    )
    if not verdict.allowed:
        return UrlIngestReport(False, url, verdict.reason, payload.status, verdict.size_bytes).as_dict()
    if payload.status != 200:
        return UrlIngestReport(False, url, "http_status", payload.status, verdict.size_bytes).as_dict()
    markdown = (convert(payload.body, payload.content_type, url) or "").strip()
    if len(markdown) < MIN_CHARS:
        return UrlIngestReport(False, url, "no_content", 200, verdict.size_bytes, len(markdown)).as_dict()
    texts = [c["text"] for c in split_doc_into_chunks(markdown, "default") if c["text"].strip()]
    if not texts:
        return UrlIngestReport(False, url, "no_chunks", 200, verdict.size_bytes, len(markdown)).as_dict()
    try:
        vectors = await embed(texts)
    except Exception as exc:
        return UrlIngestReport(False, url, "embed_unavailable", detail=str(exc)[:200]).as_dict()
    if len(vectors) != len(texts) or any(len(vector) != EMBED_DIM for vector in vectors):
        return UrlIngestReport(False, url, "wrong_dim", detail=f"expected {EMBED_DIM}").as_dict()
    provenance = {
        "url": url,
        "fetched_at": stamp,
        "licence_unknown": True,
        "content_type": payload.content_type,
        "bytes": verdict.size_bytes,
        "status": payload.status,
    }
    records = [
        {"url": url, "chunk_index": index, "text": text, "vector": vector,
         "provenance": provenance, "fetched_at": stamp}
        for index, (text, vector) in enumerate(zip(texts, vectors))
    ]
    try:
        stored = await store(records)
    except Exception as exc:
        return UrlIngestReport(False, url, "store_failed", detail=str(exc)[:200]).as_dict()
    return UrlIngestReport(
        True, url, None, payload.status, verdict.size_bytes, len(markdown), len(texts), stored, provenance,
    ).as_dict()
