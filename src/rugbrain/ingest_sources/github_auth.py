"""GitHub token resolution: file-first, typed refusal, never a silent 403.

writefix46: ``GITHUB_TOKEN`` lives in ``/srv/secrets/rugbrain.env`` but the
tree call read ``os.getenv`` only, so every ``repo_docs`` tree request went out
unauthenticated, hit the 60 req/h limit, and the run reported
``github_tree_failed status=403`` -> ``pages=0`` as if the repo were empty.

Order: env var -> ``GITHUB_TOKEN_FILE`` (path) -> well-known files. Absence is
a TYPED refusal (``blocked.auth``), not an empty list. Values are never logged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

TOKEN_ENV = "GITHUB_TOKEN"
TOKEN_FILE_ENV = "GITHUB_TOKEN_FILE"
#: File-first defaults; the first readable one that defines the key wins.
DEFAULT_TOKEN_FILES: tuple[str, ...] = ("/srv/secrets/rugbrain.env", ".env")


class GithubAuthMissing(RuntimeError):
    """No GitHub token could be resolved for an authenticated API call."""

    code = "blocked.auth"


def _from_file(path: str) -> str:
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == TOKEN_ENV:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def github_token() -> str:
    """The GitHub token from env or a credentials file; ``""`` when absent."""
    value = os.getenv(TOKEN_ENV, "").strip()
    if value:
        return value
    explicit = os.getenv(TOKEN_FILE_ENV, "").strip()
    candidates = (explicit,) if explicit else DEFAULT_TOKEN_FILES
    for path in candidates:
        if not path:
            continue
        value = _from_file(path)
        if value:
            log.debug("github_token_loaded source=file")
            return value
    return ""


def require_github_token() -> str:
    """Return the token or raise :class:`GithubAuthMissing` (``blocked.auth``)."""
    value = github_token()
    if not value:
        raise GithubAuthMissing(
            f"{GithubAuthMissing.code} reason=missing_token env={TOKEN_ENV} "
            f"files={list(DEFAULT_TOKEN_FILES)}"
        )
    return value


__all__ = [
    "DEFAULT_TOKEN_FILES",
    "GithubAuthMissing",
    "TOKEN_ENV",
    "TOKEN_FILE_ENV",
    "github_token",
    "require_github_token",
]
