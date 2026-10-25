"""Vector-space schema guard for the corpus lanes.

Root cause encoded here (2026-10-25, rb-vec26): ``news_v2_8b`` is a
**named-vector** collection -- ``{"dense": {"size": 4096, "distance": "Cosine"}}``
holding 299,069 points of Qwen/Qwen3-Embedding-8B output. A writer that posts an
*unnamed* 4096-d vector is rejected by Qdrant with HTTP 400
``Wrong input: Not existing vector name error:`` -- the dimension is right, the
write shape is wrong. Nothing in the write path checked the shape, so the whole
news lane looked broken while its vectors were perfectly usable.

The guard answers one question before any write: which vector slot does this
collection accept, and does that slot match the lane in use?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNNAMED: str = ""


class SchemaMismatch(RuntimeError):
    """The collection cannot accept vectors from the lane in use."""


@dataclass(frozen=True)
class VectorSchema:
    """One writable vector slot as declared by the collection."""

    name: str  # UNNAMED for a bare (single unnamed) config
    size: int
    distance: str

    def shape(self, vector: list[float]) -> list[float] | dict[str, list[float]]:
        """Return the vector in the shape this slot requires."""
        return vector if self.name == UNNAMED else {self.name: vector}


def _params(info: dict[str, Any]) -> dict[str, Any]:
    """Accept a raw collection info body or its ``result``/``params`` subset."""
    body = info.get("result", info)
    return body.get("config", {}).get("params", body)


def declared_schemas(info: dict[str, Any]) -> list[VectorSchema]:
    """Parse every declared vector slot. Empty list means none configured."""
    vectors = _params(info).get("vectors")
    if not isinstance(vectors, dict) or not vectors:
        return []
    if "size" in vectors:  # bare config: {"size": 4096, "distance": "Cosine"}
        return [VectorSchema(UNNAMED, int(vectors["size"]), str(vectors["distance"]))]
    return [
        VectorSchema(str(name), int(cfg["size"]), str(cfg["distance"]))
        for name, cfg in vectors.items()
        if isinstance(cfg, dict) and "size" in cfg
    ]


def guard_collection(
    info: dict[str, Any],
    *,
    lane_dim: int,
    lane_model: str = "unknown",
    vector_name: str | None = None,
    expected_distance: str = "Cosine",
) -> VectorSchema:
    """Return the slot to write, or raise :class:`SchemaMismatch`.

    ``vector_name=None`` means "use the collection's single/unnamed slot"; a
    multi-vector collection then fails loudly instead of silently picking one.
    """
    schemas = declared_schemas(info)
    if not schemas:
        raise SchemaMismatch("collection declares no vector slots")
    if vector_name is not None:
        chosen = next((s for s in schemas if s.name == vector_name), None)
        if chosen is None:
            slots = ", ".join(repr(s.name) for s in schemas)
            raise SchemaMismatch(
                f"vector name {vector_name!r} not in collection; declared slots: {slots}"
            )
    elif len(schemas) == 1:
        chosen = schemas[0]
    else:
        slots = ", ".join(repr(s.name) for s in schemas)
        raise SchemaMismatch(f"collection has multiple vector slots ({slots}); name required")

    if chosen.size != lane_dim:
        raise SchemaMismatch(
            f"vector slot {chosen.name!r} is {chosen.size}-d but lane model "
            f"{lane_model} emits {lane_dim}-d -- refusing to write a foreign vector "
            f"space (create a {lane_dim}-d collection and migrate; never dual-write)"
        )
    if chosen.distance.lower() != expected_distance.lower():
        raise SchemaMismatch(
            f"vector slot {chosen.name!r} uses distance {chosen.distance} but lane "
            f"expects {expected_distance}"
        )
    return chosen
