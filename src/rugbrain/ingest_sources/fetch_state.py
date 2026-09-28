"""Failure-vs-empty protocol for programmatic source fetches.

Why this module exists
---------------------
``list_repo_files`` / ``fetch_raw`` returned ``[]`` / ``""`` on *any* failure,
so a source that was rate-limited, 404'd, deleted or DNS-broken produced the
exact same value as a source that legitimately has nothing new. The caller
counted zero documents and exited green: the "silent death" class, where 15+
sources were dead while the fleet reported healthy ingest.

This module keeps the existing return types (every helper still degrades to
``[]``, every source still returns ``[]`` — no caller has to change) and adds
one out-of-band channel:

  * an HTTP error **records** a reason on a :mod:`contextvars` channel and
    logs it at ERROR with a machine-greppable ``reason=`` token;
  * a genuinely empty result records nothing;
  * the caller :func:`take_fetch_error` s after each fetch and turns
  "error" into a non-zero exit status.

The channel is a contextvar, not a raised exception: recording is
non-invasive, so a source keeps its own logging conventions, keeps its own
partial results, and one dead repo never aborts a 49-source batch. Every
helper here is awaited inline (no ``create_task``), so the value set in the
fetching task is the value the caller reads.

Reason tokens are deliberately distinct so an operator can tell, from one log
line, which failure they are looking at — ``github_rate_limited`` (fix:
token/quota) is not ``github_not_found`` (fix: repo moved or deleted) is not
``github_auth_invalid`` (fix: token expired/revoked).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Human-readable operator tokens. Keep them stable: they are log grep targets.
RATE_LIMITED = "github_rate_limited"
AUTH_INVALID = "github_auth_invalid"
SSO_REQUIRED = "github_sso_required"
FORBIDDEN = "github_forbidden"
NOT_FOUND = "github_not_found"
GONE = "github_gone"
SERVER_ERROR = "github_server_error"
NETWORK_ERROR = "network_error"
TIMEOUT = "network_timeout"

_RAW_NOT_FOUND = "github_raw_not_found"


@dataclass(frozen=True)
class FetchError:
    """A recorded fetch failure: machine reason + human detail."""

    reason: str
    detail: str

    def as_log_line(self) -> str:
        """One-line operator summary: ``reason=... detail=...``."""
        return f"reason={self.reason} detail={self.detail}"


_ERROR: ContextVar[FetchError | None] = ContextVar("rugbrain_fetch_error", default=None)


def classify_status(
    status: int,
    *,
    headers: Mapping[str, str] | None = None,
    body: str = "",
) -> str:
    """Map an HTTP status (+headers/body) to an operator reason token.

    The GitHub API returns 403 for *four* unrelated conditions — rate limit,
    bad token, SSO-unauthorised org, and outright denial — so a bare status
    is not a diagnosis. ``X-RateLimit-Remaining: 0`` and the documented body
    markers are what separate them.
    """
    hdr = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    low = (body or "")[:500].lower()
    if status in (401, 407):
        return AUTH_INVALID
    if status == 403:
        if hdr.get("x-ratelimit-remaining") == "0" or "rate limit" in low or "abuse" in low:
            return RATE_LIMITED
        if "bad credentials" in low or "requires authentication" in low:
            return AUTH_INVALID
        if "sso" in low or "saml" in low:
            return SSO_REQUIRED
        return FORBIDDEN
    if status in (404, 422):
        return NOT_FOUND
    if status == 410:
        return GONE
    if status == 429:
        return RATE_LIMITED
    if status >= 500:
        return SERVER_ERROR
    return f"http_status_{status}"


def classify_exception(exc: BaseException) -> str:
    """Map a transport exception to a reason token."""
    name = type(exc).__name__
    if "Timeout" in name:
        return TIMEOUT
    return NETWORK_ERROR


def raw_not_found_reason(status: int) -> str:
    """Reason token for a raw.githubusercontent.com miss.

    Kept distinct from :data:`NOT_FOUND` on purpose: raw is a *different*
    host with no auth and no rate limit, so a raw 404 does NOT prove the
    api.github.com path the code actually calls is live.
    """
    return _RAW_NOT_FOUND if status == 404 else f"github_raw_http_{status}"


def record_fetch_error(reason: str, detail: str) -> FetchError:
    """Record *detail* under *reason* on the current fetch channel + log it.

    Idempotent within one fetch: the first failure wins, because the root
    cause (e.g. rate limited) is reported, not its last symptom.
    """
    err = FetchError(reason=reason, detail=(detail or "")[:300])
    if _ERROR.get() is None:
        _ERROR.set(err)
        log.error("source_fetch_failed %s", err.as_log_line())
    return err


def take_fetch_error() -> FetchError | None:
    """Return and clear the recorded error — call once per source fetch."""
    err = _ERROR.get()
    _ERROR.set(None)
    return err


def clear_fetch_error() -> None:
    """Reset the channel before starting a fetch (stale-value hygiene)."""
    _ERROR.set(None)


__all__ = [
    "AUTH_INVALID",
    "FORBIDDEN",
    "GONE",
    "NETWORK_ERROR",
    "NOT_FOUND",
    "RATE_LIMITED",
    "SERVER_ERROR",
    "SSO_REQUIRED",
    "TIMEOUT",
    "FetchError",
    "classify_exception",
    "classify_status",
    "clear_fetch_error",
    "raw_not_found_reason",
    "record_fetch_error",
    "take_fetch_error",
]
