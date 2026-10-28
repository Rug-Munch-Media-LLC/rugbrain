"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The injected interfaces — one shape per lane, no implementation.

The orchestrator never imports a lane module directly. It receives these
callables (defaults live in :mod:`rugbrain.content_daily.adapters`), so a lane
can be swapped, faked in an acceptance run, or replaced by a hosted service
without touching the pipeline.

``collect`` pre-dates the editions (Lane B/C keep ``content_candidates``);
``write`` and ``publish`` are Lane B and Lane D; ``deliver`` is Lane E.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

#: (edition key) -> candidate stories, unranked.
CollectFn = Callable[[str], Awaitable[list[dict[str, Any]]]]
#: (story) -> importance in 0..1. Must not raise for a malformed story.
ScoreFn = Callable[[dict[str, Any]], float]
#: (stories) -> collapsed stories, best representative kept.
DedupeFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
#: (score, category, kind, duplicate) -> {"route": skip|feed|channel|both, "reason": str}.
RouteFn = Callable[[float, str, str, bool], dict[str, str]]
#: (edition key, selected stories, day) -> document dict.
WriteFn = Callable[[str, Sequence[dict[str, Any]], str], Awaitable[dict[str, Any]]]
#: (document) -> blocking problems; empty list means publishable.
EditFn = Callable[[dict[str, Any]], list[str]]
#: (document, dry_run) -> {ok, slug, url, revision, title} or {ok: False, error}.
PublishFn = Callable[[dict[str, Any], bool], Awaitable[dict[str, Any]]]
#: (edition key, document, published, dry_run) -> {ok, message_ids, method, payload}.
DeliverFn = Callable[[str, dict[str, Any], dict[str, Any], bool], Awaitable[dict[str, Any]]]

__all__ = [
    "CollectFn",
    "DedupeFn",
    "DeliverFn",
    "EditFn",
    "PublishFn",
    "RouteFn",
    "ScoreFn",
    "WriteFn",
]
