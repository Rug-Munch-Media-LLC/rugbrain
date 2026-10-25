"""The only place an ingest source reaches storage.

One job — define the store port and the single adapter that fulfils it, so no
source imports Qdrant, an embedder, or a pipeline. The adapter delegates to
the repo's one embedding path (``firehose.pipeline.IngestionPipeline``) —
it does not add a second.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorePort(Protocol):
    """Write one normalised document; return the pipeline's status dict."""

    async def ingest(
        self, collection: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


class FirehoseStore:
    """Adapter over ``rugbrain.firehose.pipeline.IngestionPipeline``.

    ``pipeline`` is injectable so a test can assert delegation without a
    Qdrant. Import is lazy: nothing here loads the embedding stack at import
    time.
    """

    def __init__(self, pipeline: Any | None = None) -> None:
        self._pipeline = pipeline

    def _target(self) -> Any:
        if self._pipeline is None:
            from rugbrain.firehose.pipeline import IngestionPipeline

            self._pipeline = IngestionPipeline()
        return self._pipeline

    async def ingest(
        self, collection: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._target().ingest(collection, content, metadata)


__all__ = ["FirehoseStore", "StorePort"]
