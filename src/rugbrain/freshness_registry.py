"""RugBrain — freshness / re-ingest tracking for continuous ingestion.

Registry of *what* was ingested, *when*, and at which *source version*, so
stale sources can be re-ingested on a schedule:

  * GitHub-backed sources re-ingest when their repo updates (version =
    HEAD commit SHA + commit timestamp — see ``git_head_version``).
  * News / post-mortem sources re-ingest periodically when
    ``max_age_seconds`` elapses since the last successful ingest.

Storage is an in-memory dict, optionally persisted to a JSON file
(``pathlib``) so the registry survives process restarts without external
services. Failed runs do NOT advance ``last_ingested_at`` (same contract as
the scheduler checkpoints) so they are retried on the next pass.

Usage::

    reg = FreshnessRegistry(path="data/freshness.json")
    if reg.should_reingest("slowmist", max_age_seconds=6 * 3600,
                           current_version=git_head_version("/srv/repos/slowmist")):
        ...  # fetch, score, ingest
        reg.record("slowmist", version=git_head_version(...), count=n)
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)


def now_utc() -> float:
    """Unix timestamp for registry bookkeeping."""
    return time.time()


def git_head_sha(repo_dir: str, timeout: int = 30) -> str | None:
    """Return the HEAD commit SHA of a git repo, or None on any failure."""
    try:
        result = subprocess.run(  # noqa: S603 — repo_dir is caller-provided
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        sha = result.stdout.strip()
        return sha or None
    except Exception as e:
        log.debug("git_head_sha_error repo=%s err=%s", repo_dir, str(e)[:120])
        return None


def git_head_version(repo_dir: str, timeout: int = 30) -> str | None:
    """Version string for a git-backed source: ``<sha>:<commit-time>``.

    Changes whenever the repo updates, so ``should_reingest`` with
    ``current_version=git_head_version(...)`` re-ingests GitHub-based
    sources exactly when their upstream changes. Returns None when the
    repo is not a valid git checkout.
    """
    sha = git_head_sha(repo_dir, timeout=timeout)
    if sha is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", repo_dir, "log", "-1", "--format=%cI"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stamp = result.stdout.strip()
    except Exception as e:
        log.debug("git_head_version_stamp_error repo=%s err=%s", repo_dir, str(e)[:120])
        stamp = ""
    return f"{sha}:{stamp}" if stamp else sha


class IngestRecord(BaseModel):
    """One row in the freshness registry: a source's last ingest state."""

    source: str
    collection: str = ""
    last_ingested_at: float = 0.0
    version: str = ""
    last_count: int = 0
    last_status: str = "ok"
    error: str = ""

    def is_stale(
        self,
        max_age_seconds: int,
        *,
        current_version: str | None = None,
        now: float | None = None,
    ) -> bool:
        """True when the source should be re-ingested.

        Stale = never ingested, last run failed, source version changed, or
        ``max_age_seconds`` elapsed since the last successful ingest.
        """
        if self.last_status == "error":
            return True
        if current_version is not None and current_version != self.version:
            return True
        return (now if now is not None else now_utc()) - (
            self.last_ingested_at or 0.0
        ) >= max_age_seconds


class FreshnessRegistry:
    """Track per-source ingest state, persisted to JSON when a path is given.

    Failed runs are recorded via ``mark_error`` which does NOT advance
    ``last_ingested_at`` — mirroring the scheduler checkpoint contract so
    failures are retried on the next pass.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._records: dict[str, IngestRecord] = {}
        if self._path is not None and self._path.exists():
            self.load()

    # ── Accessors ───────────────────────────────────────────────────────

    def get(self, source: str) -> IngestRecord | None:
        return self._records.get(source)

    def all_records(self) -> dict[str, IngestRecord]:
        return dict(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, source: object) -> bool:
        return source in self._records

    # ── Mutations ───────────────────────────────────────────────────────

    def record(
        self,
        source: str,
        *,
        collection: str = "",
        version: str | None = None,
        count: int = 0,
        status: str = "ok",
        now: float | None = None,
    ) -> IngestRecord:
        """Upsert a successful (or explicitly-statused) ingest for *source*.

        Advances ``last_ingested_at`` so a healthy source is not re-fetched
        on every loop pass.
        """
        rec = IngestRecord(
            source=source,
            collection=collection,
            last_ingested_at=now if now is not None else now_utc(),
            version=version or "",
            last_count=count,
            last_status=status,
        )
        self._records[source] = rec
        self._persist()
        return rec

    def mark_error(self, source: str, message: str) -> IngestRecord:
        """Record a failed pass for *source* without advancing freshness.

        Keeps the previous ``last_ingested_at``/``version`` so
        ``should_reingest`` returns True on the next pass (retry).
        """
        prev = self.get(source)
        rec = IngestRecord(
            source=source,
            collection=prev.collection if prev else "",
            last_ingested_at=prev.last_ingested_at if prev else 0.0,
            version=prev.version if prev else "",
            last_count=prev.last_count if prev else 0,
            last_status="error",
            error=message,
        )
        self._records[source] = rec
        self._persist()
        return rec

    def should_reingest(
        self,
        source: str,
        *,
        max_age_seconds: int,
        current_version: str | None = None,
        now: float | None = None,
    ) -> bool:
        """True when *source* has never been ingested or is stale."""
        rec = self.get(source)
        if rec is None:
            return True
        return rec.is_stale(max_age_seconds, current_version=current_version, now=now)

    def stale_sources(
        self,
        sources: list[str],
        *,
        max_age_seconds: int,
        versions: dict[str, str | None] | None = None,
        now: float | None = None,
    ) -> list[str]:
        """Subset of *sources* that need re-ingestion right now."""
        versions = versions or {}
        return [
            name
            for name in sources
            if self.should_reingest(
                name,
                max_age_seconds=max_age_seconds,
                current_version=versions.get(name),
                now=now,
            )
        ]

    def clear(self) -> None:
        self._records.clear()
        self._persist()

    # ── Persistence ─────────────────────────────────────────────────────

    def load(self) -> None:
        """Load records from the JSON file (tolerant of corruption)."""
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = {
                name: IngestRecord.model_validate(data)
                for name, data in payload.items()
                if isinstance(data, dict)
            }
        except (OSError, json.JSONDecodeError, ValueError) as e:
            log.warning(
                "freshness_registry_load_failed path=%s err=%s — starting empty",
                self._path,
                str(e)[:120],
            )
            self._records = {}

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {name: rec.model_dump() for name, rec in self._records.items()}
            self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as e:
            log.warning("freshness_registry_save_failed path=%s err=%s", self._path, str(e)[:120])


__all__ = [
    "FreshnessRegistry",
    "IngestRecord",
    "git_head_sha",
    "git_head_version",
    "now_utc",
]
