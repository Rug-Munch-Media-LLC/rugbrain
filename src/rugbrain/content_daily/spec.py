"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The stage contract for the three-per-day content editions.

One table, imported by the orchestrator, the CLI and the acceptance harness.
Every stage names what it consumes, what it produces, the key that makes a
retry safe, how it fails, and how often it may be retried. The pipeline may
not invent a stage absent from :data:`STAGES` — an undeclared step is a step
whose failure mode nobody reviewed.

Stages, in order::

    collect -> score_dedupe -> select -> write -> edit_factcheck
            -> publish_site -> deliver_telegram -> record_metrics

The two publication stages are ordered on purpose. A Telegram teaser carries
the site URL, so delivering before the site write succeeds would ship a link
to a page that does not exist — the half-publish failure documented in
``rmi-content-pipeline`` (``docs/CONTENT-CHAIN.md`` §3). ``publish_site`` is
therefore fatal and ``deliver_telegram`` is not.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Stable stage identifiers — journal rows and dashboards key on these.
COLLECT = "collect"
SCORE_DEDUPE = "score_dedupe"
SELECT = "select"
WRITE = "write"
EDIT = "edit_factcheck"
PUBLISH = "publish_site"
DELIVER = "deliver_telegram"
RECORD = "record_metrics"


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, how far apart, and what is worth retrying.

    ``retry_on`` empty means "every failure"; a populated tuple means only the
    named error classes are retried and everything else fails on attempt one
    (a schema violation is not made better by waiting).
    """

    attempts: int = 1
    base_delay: float = 0.0
    max_delay: float = 0.0
    timeout: float | None = None
    retry_on: tuple[str, ...] = ()

    def wants_retry(self, error_class: str) -> bool:
        """True when ``error_class`` is retryable under this policy."""
        return not self.retry_on or error_class in self.retry_on


@dataclass(frozen=True)
class StageSpec:
    """One declared step: its interface, its key, its failure mode."""

    name: str
    consumes: str
    produces: str
    idempotency_key: str
    failure_mode: str
    policy: RetryPolicy
    fatal: bool


STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        name=COLLECT,
        consumes="edition key, candidate limit",
        produces="stories[] from the candidate pool",
        idempotency_key="daily:{edition}:{day}:collect (read-only)",
        failure_mode="empty pool -> status no_candidates, nothing published",
        policy=RetryPolicy(
            attempts=2,
            base_delay=2.0,
            max_delay=8.0,
            timeout=120.0,
            retry_on=("timeout", "connection", "http_5xx"),
        ),
        fatal=True,
    ),
    StageSpec(
        name=SCORE_DEDUPE,
        consumes="stories[]",
        produces="scored, collapsed pool + dedupe drop counts",
        idempotency_key="daily:{edition}:{day}:score_dedupe (pure, in-memory)",
        failure_mode="scorer unavailable -> importance field fallback; never aborts",
        policy=RetryPolicy(attempts=1, timeout=60.0),
        fatal=False,
    ),
    StageSpec(
        name=SELECT,
        consumes="scored pool + published ledger",
        produces="selected[] routed to channel|both, ledger-suppressed refs dropped",
        idempotency_key="daily:{edition}:{day}:select (deterministic rank)",
        failure_mode="no item clears the route floor -> status no_candidates",
        policy=RetryPolicy(attempts=1, timeout=30.0),
        fatal=True,
    ),
    StageSpec(
        name=WRITE,
        consumes="selected[] + edition angle",
        produces="doc: title, edition_title, slug, url, sections, sources, telegram",
        idempotency_key="daily:{edition}:{day}:write (same topic, same day)",
        failure_mode="writer silence -> deterministic sections; guard failure -> needs_review",
        policy=RetryPolicy(
            attempts=3,
            base_delay=5.0,
            max_delay=20.0,
            timeout=180.0,
            retry_on=("timeout", "connection", "http_429", "http_5xx"),
        ),
        fatal=True,
    ),
    StageSpec(
        name=EDIT,
        consumes="doc",
        produces="problems[] (schema, citation, style, layout, numbers)",
        idempotency_key="daily:{edition}:{day}:edit (pure)",
        failure_mode="any problem -> status needs_review, nothing published",
        policy=RetryPolicy(attempts=1, timeout=60.0),
        fatal=True,
    ),
    StageSpec(
        name=PUBLISH,
        consumes="doc",
        produces="{slug, url, revision, title}",
        idempotency_key="slug (store upsert bumps revision, never a second URL)",
        failure_mode="retries exhausted -> status site_failed; Telegram is NOT sent",
        policy=RetryPolicy(
            attempts=3,
            base_delay=5.0,
            max_delay=60.0,
            timeout=120.0,
            retry_on=("timeout", "connection", "http_429", "http_5xx"),
        ),
        fatal=True,
    ),
    StageSpec(
        name=DELIVER,
        consumes="doc + published url",
        produces="{message_ids[], method}",
        idempotency_key="daily:{edition}:{day}:tg:{channel} (recorded in the ledger)",
        failure_mode="retries exhausted -> status partial; site stays published, no re-post",
        policy=RetryPolicy(
            attempts=3,
            base_delay=5.0,
            max_delay=30.0,
            timeout=60.0,
            retry_on=("timeout", "connection", "http_429", "http_5xx"),
        ),
        fatal=False,
    ),
    StageSpec(
        name=RECORD,
        consumes="run record",
        produces="one JSONL row + the health line",
        idempotency_key="daily:{edition}:{day}:record (append-only)",
        failure_mode="never raises; a lost line is reported on stderr, run stays valid",
        policy=RetryPolicy(attempts=1, timeout=5.0),
        fatal=False,
    ),
)

_BY_NAME: dict[str, StageSpec] = {stage.name: stage for stage in STAGES}


def spec(name: str) -> StageSpec:
    """The declared contract for ``name``; unknown stages are a programming error."""
    try:
        return _BY_NAME[name]
    except KeyError:  # pragma: no cover - guards a typo, not a runtime condition
        raise KeyError(f"undeclared stage {name!r}; declared: {sorted(_BY_NAME)}") from None


__all__ = [
    "COLLECT",
    "DELIVER",
    "EDIT",
    "PUBLISH",
    "RECORD",
    "SCORE_DEDUPE",
    "SELECT",
    "STAGES",
    "WRITE",
    "RetryPolicy",
    "StageSpec",
    "spec",
]
