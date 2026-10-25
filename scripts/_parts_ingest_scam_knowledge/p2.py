"""Part 2/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _point_id(source: str, rel_key: str, content_hash: str) -> str:
    """Deterministic UUIDv5 point ID — idempotent across re-runs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"rag:{source}:{rel_key}:{content_hash}"))


class Checkpoint:
    """JSON checkpoint tracking which source files have been ingested.

    Layout: {"sources": {"<source>": {"<rel_key>": "<sha256>"}},
             "last_updated": {"<source>": "<iso>"}}.
    A file is skipped when its current sha256 matches the checkpoint.
    """

    def __init__(self, path: Path):
        self.path = path
        # Layout: {"sources": {"<source>": {"<rel_key>": "<sha256>"}},
        #          "last_updated": {"<source>": "<iso>"}}
        self.data: dict[str, Any] = {"sources": {}, "last_updated": {}}

    def load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_sources = loaded.get("sources")
                    if isinstance(raw_sources, dict):
                        cleaned: dict[str, dict[str, str]] = {}
                        for src, files in raw_sources.items():
                            if isinstance(src, str) and isinstance(files, dict):
                                cleaned[src] = {
                                    str(k): str(v) for k, v in files.items() if isinstance(k, str)
                                }
                        self.data["sources"] = cleaned
                    raw_last = loaded.get("last_updated")
                    if isinstance(raw_last, dict):
                        self.data["last_updated"] = {
                            str(k): str(v) for k, v in raw_last.items() if isinstance(k, str)
                        }
            except (json.JSONDecodeError, OSError, TypeError):
                logger.warning("checkpoint unreadable, starting fresh: %s", self.path)
                self.data = {"sources": {}, "last_updated": {}}
        self.data.setdefault("sources", {})
        self.data.setdefault("last_updated", {})

    def is_done(self, source: str, rel_key: str, text: str) -> bool:
        current = self.data["sources"].get(source, {}).get(rel_key)
        return bool(current) and current == _content_hash(text)

    def mark(self, source: str, rel_key: str, text: str) -> None:
        self.data.setdefault("sources", {})
        self.data["sources"].setdefault(source, {})[rel_key] = _content_hash(text)

    def set_last_updated(self, source: str, stamp: str) -> None:
        self.data.setdefault("last_updated", {})
        self.data["last_updated"][source] = stamp

    def get_last_updated(self, source: str) -> str:
        return str(self.data.get("last_updated", {}).get(source, ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


async def _embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed texts on the bulk lane (DeepInfra-first, local fallback).

    Returns L2-normalized vectors aligned with input order (None on failure).
    The lane lives in ``scripts/embed_lane.py`` so heavy ingestion does not
    saturate the local llama.cpp server.
    """
    if not texts:
        return []
    from embed_lane import embed_batch as _lane_embed_batch

    vectors = await _lane_embed_batch(texts)
    results: list[list[float] | None] = []
    for vec in vectors:
        if vec is None:
            results.append(None)
            continue
        arr = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n > 0:
            arr = arr / n
        results.append(arr.tolist())
    return results


async def _ensure_collection(client: httpx.AsyncClient, name: str) -> bool:
    try:
        r = await client.get(f"/collections/{name}")
        if r.status_code == 200:
            return True
    except Exception:
        logger.debug("ensure_collection_check_failed %s", name)
    try:
        payload = {"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}
        r = await client.put(f"/collections/{name}", json=payload)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error("create_collection_fail %s: %s", name, e)
        return False


async def _upsert_batch(
    client: httpx.AsyncClient,
    collection: str,
    points: list[dict[str, Any]],
) -> int:
    """Upsert up to 50 points in one REST call. Returns success count."""
    if not points:
        return 0
    ok = 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        try:
            r = await client.put(f"/collections/{collection}/points", json={"points": batch})
            if r.status_code in (200, 201):
                ok += len(batch)
            else:
                logger.warning("upsert_fail status=%d body=%s", r.status_code, r.text[:300])
        except Exception as e:
            logger.warning("upsert_error: %s", e)
    return ok


__all__ = [
    "Checkpoint",
    "_content_hash",
    "_embed_batch",
    "_ensure_collection",
    "_point_id",
    "_upsert_batch",
]
