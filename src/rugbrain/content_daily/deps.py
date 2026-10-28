"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The dependency bundle: one callable per lane, swappable per run.
"""

from __future__ import annotations

from dataclasses import dataclass

from rugbrain.content_daily import adapters, delivery
from rugbrain.content_daily.ports import (
    CollectFn,
    DedupeFn,
    DeliverFn,
    EditFn,
    PublishFn,
    RouteFn,
    ScoreFn,
    WriteFn,
)


@dataclass(frozen=True)
class Deps:
    """Every lane's entry point, swappable per run (and in the acceptance harness)."""

    collect: CollectFn = adapters.collect_stories
    score: ScoreFn = adapters.score_story
    dedupe: DedupeFn = adapters.dedupe_stories
    route: RouteFn = adapters.route_story
    write: WriteFn = adapters.write_edition
    edit: EditFn = adapters.edit_document
    publish: PublishFn = delivery.publish_document
    deliver: DeliverFn = delivery.deliver_telegram


__all__ = ["Deps"]
