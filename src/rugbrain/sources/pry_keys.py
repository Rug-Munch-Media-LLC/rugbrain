"""Resolve Pry's owner key.

Split out of pry_scraper.py to clear the 200-line structure cap. The key lives
in the running container's environment and in no tracked file, so a fresh checkout
has no other way to obtain it. Returning None rather than raising lets the caller
produce one actionable message instead of a stack trace from inside a credential
lookup.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

#: Absolute path. Ruff S607 forbids a partial executable path, and the rule is
#: right: a bare "docker" resolves against PATH, so a shim earlier in PATH would
#: be executed with this code's privileges.
DOCKER = "/usr/bin/docker"


def container_owner_key() -> str | None:
    """Read PRY_API_KEY from the running `pry` container, or None."""
    try:
        out = subprocess.run(  # noqa: S603 - literal argv, no shell, no user input
            [DOCKER, "inspect", "pry", "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("pry_container_inspect_failed", extra={"error": str(exc)})
        return None
    for line in out.stdout.splitlines():
        if line.startswith("PRY_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None
