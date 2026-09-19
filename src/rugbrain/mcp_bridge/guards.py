"""Guardrail plumbing for the MCP bridge (sanitize in, redact out).

All lookups of ``_GUARDRAILS_OK`` / ``sanitize_query`` / ``redact_output``
go through the package namespace at call time so tests can monkeypatch
``rugbrain.mcp_bridge.*`` attributes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _guard_query(query: str) -> tuple[str, dict[str, Any]]:
    """Sanitize *query* for prompt injection.

    Returns ``(safe_query, guard_meta)``. ``guard_meta`` carries
    ``flagged`` / ``reasons`` / ``sanitized`` / ``refused``; ``refused``
    is True when the query was flagged AND nothing usable remains after
    stripping (callers should fail closed and NOT forward). Never raises.
    """
    from rugbrain import mcp_bridge as _pkg

    if not _pkg._GUARDRAILS_OK:
        return query, {"flagged": False, "reasons": [], "sanitized": False, "refused": False}
    try:
        sq = _pkg.sanitize_query(query)
        safe = sq.query
        # Fail-closed: refuse when the flagged query strips to nothing OR a
        # trivial stub (<=3 chars) — forwarding a bare "and" would return
        # garbage while still having touched the MCP.
        refused = sq.flagged and len(safe.strip()) <= 3
        return safe, {
            "flagged": sq.flagged,
            "reasons": list(sq.reasons),
            "sanitized": safe != query,
            "refused": refused,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("guardrails sanitize failed: %s", str(exc)[:120])
        return query, {"flagged": False, "reasons": [], "sanitized": False, "refused": False}


def _redact_text(value: Any) -> Any:
    """Apply ``redact_output`` to a string (passthrough on failure)."""
    from rugbrain import mcp_bridge as _pkg

    if isinstance(value, str) and _pkg._GUARDRAILS_OK:
        try:
            return _pkg.redact_output(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("guardrails redact failed: %s", str(exc)[:120])
    return value


def _redact_result(result: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return *result* with the given string fields redacted (never mutates)."""
    if not result:
        return result
    out = dict(result)
    for key in keys:
        if key in out:
            out[key] = _redact_text(out[key])
    return out
