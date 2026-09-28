"""Boot runner primitives — the mechanics of starting one subsystem.

One job: given a module name and a set of symbol names, import lazily, start,
record status into ``health_state``, and stay silent on failure. The *table*
of what to start lives in ``boot_steps``; this module has no knowledge of it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rugbrain.app.health_state import set_flag

logger = logging.getLogger(__name__)

#: Background asyncio tasks kept alive for the process lifetime (RUF006).
#: Populated by :func:`spawn_loop`; the lifespan never awaits them.
BACKGROUND_TASKS: list[asyncio.Task[Any]] = []


async def spawn_loop(module: str, symbol: str) -> None:
    """Import ``module.symbol`` and run it, awaiting the returned coroutine."""
    mod = __import__(module, fromlist=[symbol])
    await getattr(mod, symbol)()


def spawn_task(module: str, symbol: str) -> bool:
    """Start a bare scan loop as a background task. Returns True if spawned."""
    try:
        BACKGROUND_TASKS.append(asyncio.ensure_future(spawn_loop(module, symbol)))
    except Exception as exc:
        logger.warning("%s_start_failed: %s", module, exc)
        return False
    return True


async def start_managed(
    module: str,
    start_sym: str,
    running_sym: str,
    flag: str,
    stats: tuple[str, str] | None = None,
) -> bool:
    """Start a subsystem with an explicit stop handle; record its status."""
    try:
        mod = __import__(module, fromlist=[start_sym])
        await getattr(mod, start_sym)()
        ok = bool(getattr(mod, running_sym)())
    except Exception as exc:
        logger.warning("%s_start_failed: %s", module, exc)
        set_flag(flag, False)
        return False
    set_flag(flag, ok)
    if stats is not None:
        try:
            set_flag(stats[0], getattr(mod, stats[1])())
        except Exception as exc:
            logger.debug("%s stats unavailable: %s", stats[0], exc)
    logger.info("%s status=%s", module, "ok" if ok else "fail")
    return ok


async def stop_managed(module: str, stop_sym: str) -> None:
    """Stop a managed subsystem; a missing module or function is not an error."""
    try:
        mod = __import__(module, fromlist=[stop_sym])
        await getattr(mod, stop_sym)()
    except Exception:
        logger.debug("%s stop skipped", stop_sym)


def flag_tasks(names: tuple[str, ...]) -> None:
    """Mark a batch of bare scan loops as running in the health state."""
    for name in names:
        set_flag(name, True)
