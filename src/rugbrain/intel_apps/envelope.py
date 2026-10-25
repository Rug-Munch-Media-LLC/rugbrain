"""One envelope shape for every optional intel provider."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

STATUS_OK = "ok"
STATUS_REFUSED = "refused"


def envelope(
    *,
    provider: str,
    tool: str,
    status: str,
    latency_ms: int = 0,
    data: Any = None,
    code: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the single answer shape: ok carries data, refused names the provider."""
    error = None
    if status != STATUS_OK:
        if not code or not reason:
            raise ValueError("refusal requires code and reason")
        if provider not in reason:
            raise ValueError("refusal reason must name the provider")
        error = {"code": code, "reason": reason}
    return {
        "status": status,
        "provider": provider,
        "tool": tool,
        "data": data if status == STATUS_OK else None,
        "error": error,
        "latency_ms": max(0, int(latency_ms)),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
