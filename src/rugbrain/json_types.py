"""JSON value types shared by public signatures (replaces bare ``Any``).

Payloads crossing the monitor/API boundary are JSON documents, not arbitrary
Python objects; these aliases state that contract without changing runtime
behaviour (they are pure type aliases).
"""

from __future__ import annotations

from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
