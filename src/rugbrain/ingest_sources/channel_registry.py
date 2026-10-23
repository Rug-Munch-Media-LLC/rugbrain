"""The one place a discovered handle is appended to a channel list.

``tg_channels.txt`` is written by two discovery paths (the userbot join path
and the joined-dialog path) and read by several sweepers. Before this module
each writer rolled its own ``open(..., "a")`` and its own idea of "already
there", so ``@Handle`` and ``handle`` could both land and the same channel was
swept twice — the mirror of the same text across channels then cost an embed
per copy.

Dedupe is case-insensitive and ``@``-insensitive because Telegram usernames
are: ``@WatcherGuru`` and ``watcherguru`` are one channel.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

COMMENT_PREFIX = "#"


def normalise(handle: str) -> str:
    """Return the canonical form of a handle (no ``@``, no stray whitespace)."""
    return handle.strip().lstrip("@").strip()


def read_channels(path: str | Path) -> list[str]:
    """Read an existing list, dropping blanks and ``#`` comment lines."""
    try:
        with open(path) as f:
            return [
                h for h in (normalise(line) for line in f) if h and not h.startswith(COMMENT_PREFIX)
            ]
    except OSError:
        return []


def append_channels(handles: Iterable[str], path: str | Path) -> list[str]:
    """Append the handles not already present; return exactly what was added.

    The existing file is the source of truth for "already present" — including
    entries a human added by hand — so a re-run of any discovery path is a
    no-op. Matching is case-insensitive, which is what makes a re-run safe
    after a run that wrote ``@Handle`` instead of ``handle``.
    """
    existing = read_channels(path)
    seen = {h.lower() for h in existing}
    added: list[str] = []
    for raw in handles:
        handle = normalise(raw)
        if not handle or handle.startswith(COMMENT_PREFIX) or handle.lower() in seen:
            continue
        seen.add(handle.lower())
        added.append(handle)
    if not added:
        return []
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or _ends_with_newline(target) else "\n"
    with open(target, "a") as f:
        f.write(prefix + "\n".join(added) + "\n")
    return added


def _ends_with_newline(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            return f.read(1) == b"\n"
    except OSError:
        return True


__all__ = ["append_channels", "normalise", "read_channels"]
