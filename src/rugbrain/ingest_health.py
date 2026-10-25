"""Ingest health surface: makes a silent ingest failure visible as degraded readiness."""

from __future__ import annotations

import logging
import time

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

FAILURES_TOTAL = Counter(
    "rugbrain_ingest_failures_total", "Ingest failures by source and stage", ["source", "stage"]
)
SUCCESS_TOTAL = Counter("rugbrain_ingest_success_total", "Successful ingests by source", ["source"])
LAST_SUCCESS_TS = Gauge(
    "rugbrain_ingest_last_success_timestamp_seconds", "Unix ts of last successful ingest", ["source"]
)
SILENT_FAILURES = Gauge(
    "rugbrain_ingest_silent_failures", "Ingest exceptions suppressed since start", ["source"]
)

_FAILURE_COUNTS: dict[str, int] = {}


class IngestHealth:
    """Record every ingest outcome; a swallowed exception must never be invisible."""

    @staticmethod
    def failure(source: str, stage: str, error: BaseException) -> None:
        FAILURES_TOTAL.labels(source=source, stage=stage).inc()
        count = _FAILURE_COUNTS.get(source, 0) + 1
        _FAILURE_COUNTS[source] = count
        SILENT_FAILURES.labels(source=source).set(count)
        logger.error(
            "ingest_failure", extra={"source": source, "stage": stage, "error_type": type(error).__name__}
        )

    @staticmethod
    def success(source: str) -> None:
        SUCCESS_TOTAL.labels(source=source).inc()
        LAST_SUCCESS_TS.labels(source=source).set(time.time())

    @staticmethod
    def readiness() -> dict[str, object]:
        failures = dict(_FAILURE_COUNTS)
        return {
            "status": "degraded" if failures else "ok",
            "failures": failures,
            "total_failures": sum(failures.values()),
        }
