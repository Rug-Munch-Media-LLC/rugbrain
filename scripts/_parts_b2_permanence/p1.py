"""Part 1/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403

def resolve_qdrant_client(host: str | None, port: int | None) -> qdrant_client.QdrantClient:
    """Build a QdrantClient, trying the fallback chain until one answers.

    Chain: explicit --qdrant-url → QDRANT_URL → 127.0.0.1:port →
    localhost:port. Returns a client for the first reachable URL.
    """
    port_str = str(port or DEFAULT_QDRANT_PORT)
    candidates: list[str] = []
    if host:
        candidates.append(host)
    candidates.extend(
        [
            DEFAULT_QDRANT_HOST,
            f"http://127.0.0.1:{port_str}",
            f"http://localhost:{port_str}",
        ]
    )

    seen: set[str] = set()
    errors: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            client = qdrant_client.QdrantClient(url=url, timeout=30)
            _ = client.get_collections()
            logger.info("qdrant_connected url=%s", url)
            return client
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            logger.debug("qdrant_probe_failed url=%s err=%s", url, exc)

    raise RuntimeError("Cannot reach Qdrant — tried " + "; ".join(errors))


def list_collections(client: qdrant_client.QdrantClient) -> list[str]:
    """Return collection names sorted alphabetically."""
    return sorted(c.name for c in client.get_collections().collections)


def filter_collections(collections: list[str], excluded_suffixes: tuple[str, ...]) -> list[str]:
    """Exclude generated mirror collections from the canonical snapshot set."""
    suffixes = tuple(suffix for suffix in excluded_suffixes if suffix)
    if not suffixes:
        return list(collections)
    return [name for name in collections if not name.endswith(suffixes)]


def resolve_payload_only_collections(explicit: list[str] | None) -> set[str]:
    """Resolve collections whose vectors are too large for the nightly window.

    LINEAGE-AWARE (2026-09-28). This used to return the configured names
    verbatim, and that became a landmine the moment the writer started shipping
    `_8b` twins instead of base collections.

    B2_PAYLOAD_ONLY_COLLECTIONS names `codebase_reference_v2`. The writer now
    snapshots `codebase_reference_v2_8b` for that lineage, and
    collection_has_vectors() tests membership by exact name — so the twin was
    NOT payload-only. with_vectors defaults to True and the unit passes no
    arguments, so the canonical collection would have exported its VECTORS:
    2,126,630 points x 4096 floats, roughly 35 GB for one collection, in a job
    capped at 4G RAM and a 45-minute window.

    A payload-only decision is a property of the CONTENT, not of the collection
    name — the twin holds the same chunks with a different embedding, so if the
    base is payload-only the twin must be too. Every configured name therefore
    expands to itself plus its canonical suffix variant, and any live collection
    that either maps onto a configured name is covered regardless of which
    generation is currently being shipped.
    """
    raw = (
        explicit
        if explicit is not None
        else os.getenv(
            "B2_PAYLOAD_ONLY_COLLECTIONS", ",".join(DEFAULT_PAYLOAD_ONLY_COLLECTIONS)
        ).split(",")
    )
    configured = {part.strip() for part in raw if part.strip()}
    suffix = os.getenv("B2_CANONICAL_SUFFIX", "_8b")

    # A name is payload-only if it, its twin, or its base is configured. This is
    # deliberately symmetric so the set is correct whichever generation the
    # selection logic picks on a given day.
    expanded = set(configured)
    for name in configured:
        if name.endswith(suffix):
            expanded.add(name[: -len(suffix)])
        else:
            expanded.add(name + suffix)
    return expanded


def collection_has_vectors(name: str, requested: bool, payload_only: set[str]) -> bool:
    """Return whether this collection should export vector data."""
    return requested and name not in payload_only


def collection_info(client: qdrant_client.QdrantClient, name: str) -> dict[str, Any]:
    """Return vector size/distance metadata for the manifest."""
    try:
        info = client.get_collection(collection_name=name)
        vectors = info.config.params.vectors
        size: object = getattr(vectors, "size", None)
        distance: object = getattr(vectors, "distance", None)
        return {"vector_size": size, "distance": str(distance) if distance else None}
    except Exception as exc:
        logger.warning("collection_info_failed collection=%s err=%s", name, exc)
        return {"vector_size": None, "distance": None}


__all__ = [
    "resolve_qdrant_client",
    "list_collections",
    "filter_collections",
    "resolve_payload_only_collections",
    "collection_has_vectors",
    "collection_info",
]
