"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Digestion — read a document or news item, extract, write, link.

The entry point for the "read and digest documents and news" mandate. One
job: take an untrusted blob of text plus its provenance, and produce

  1. a Qdrant point in the right collection with the right payload shape,
  2. a knowledge-graph episode linked to every entity found.

This module is a **facade**. Each responsibility behind it lives in one
module with one job:

  * ``routing``        — which Qdrant collection a document belongs in
  * ``payload``        — the point id and the payload contract
  * ``digest_document``— the end-to-end call and its result type
  * ``entities``       — what counts as an entity
  * ``graph``          — the Neo4j episode write
  * ``security``       — sanitise in, redact out

``from rugbrain.digest import X`` is the supported surface and is
unchanged by this split.
"""

from __future__ import annotations

from rugbrain.digest.digest_document import (
    DigestResult,
    QdrantWriter,
    digest_document,
)
from rugbrain.digest.graph import GraphWriteResult
from rugbrain.digest.payload import build_payload, stable_point_id
from rugbrain.digest.routing import (
    COLLECTION_NEWS,
    COLLECTION_SCAM_INTEL,
    COLLECTION_SCAMS,
    COLLECTION_THREAT,
    WRITABLE_COLLECTIONS,
    route_collection,
)

__all__ = [
    "COLLECTION_NEWS",
    "COLLECTION_SCAMS",
    "COLLECTION_SCAM_INTEL",
    "COLLECTION_THREAT",
    "WRITABLE_COLLECTIONS",
    "DigestResult",
    "GraphWriteResult",
    "QdrantWriter",
    "build_payload",
    "digest_document",
    "route_collection",
    "stable_point_id",
]
