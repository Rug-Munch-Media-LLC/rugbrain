"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Entity extraction for ingested documents and news — public surface.

This module is a **facade**. The responsibilities behind it each live in
one module with one job:

  * ``lexicons``      — the word lists (data only)
  * ``entity_shapes`` — address / domain / ticker shape recognisers
  * ``severity``      — text to a severity level
  * ``entity_types``  — the EntitySet value type and its serialisation
  * ``entity_extract``— the orchestrator that assembles an EntitySet

``from rugbrain.digest.entities import X`` keeps working for every
existing caller, including the graph layer's ``as_dict`` duck-type, so
the split is a package-internal detail rather than a contract change.

**None of this content is ever interpreted.** Extracted strings are
treated as opaque identifiers and written to Qdrant/Neo4j as data, never
as instructions. See ``security.py`` for the sanitisation applied on the
way in and out.
"""

from __future__ import annotations

from rugbrain.digest.entity_extract import extract
from rugbrain.digest.entity_shapes import evm_checksum
from rugbrain.digest.entity_types import EntitySet
from rugbrain.digest.severity import classify_severity

__all__ = ["EntitySet", "classify_severity", "evm_checksum", "extract"]
