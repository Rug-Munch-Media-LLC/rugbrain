"""Extracted from the original module — moved verbatim."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def dialog_entities(client: Any) -> dict[str, Any]:
    """username -> entity for every channel the ALT account is already in.

    iter_dialogs() needs no username resolution, so the sweep reads joined
    channels without touching the ResolveUsername rate limit at all. Channels
    the account is NOT in still fall back to get_entity (and its errors).
    """
    out: dict[str, Any] = {}
    try:
        async for d in client.iter_dialogs():
            if d.is_channel:
                username = getattr(d.entity, "username", None)
                if username:
                    out[username.lower()] = d.entity
    except Exception as exc:  # degrade to per-handle resolution
        logger.warning("dialog_scan_failed err=%s", str(exc)[:120])
    return out
