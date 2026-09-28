"""API-key authentication middleware — fail closed.

One job: decide whether a request may proceed, based on the ``X-API-Key``
header and the configured key. ``/health*`` and ``/ready*`` are exempt. When no
key is configured the middleware refuses everything with 503 unless
``DEV_MODE=true``.

The configured key may hold ``"new,old"`` during a zero-downtime rollover;
every listed key validates, compared in constant time.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from rugbrain.app.config import DEV_MODE, RMI_RAG_API_KEY

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = ("/health", "/ready", "/api/v1/health")
HEADER = "X-API-Key"


class RAGAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate ``X-API-Key``; fail closed when unconfigured."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        path = request.url.path
        if path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        if not RMI_RAG_API_KEY and not DEV_MODE:
            logger.error("auth_refused path=%s reason=key_not_configured", path)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Service unavailable — RMI_RAG_API_KEY not configured",
                    "code": "auth.not_configured",
                },
            )

        if not RMI_RAG_API_KEY:
            return await call_next(request)

        api_key = request.headers.get(HEADER, "")
        if not api_key:
            logger.warning("auth_failed path=%s reason=missing_api_key", path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": f"Unauthorized — {HEADER} header required",
                    "code": "auth.missing_key",
                },
            )

        if not any(
            hmac.compare_digest(api_key.encode(), k.encode())
            for k in (s.strip() for s in RMI_RAG_API_KEY.split(","))
            if k
        ):
            logger.warning("auth_failed path=%s reason=invalid_api_key", path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": f"Unauthorized — invalid {HEADER}",
                    "code": "auth.invalid_key",
                },
            )

        return await call_next(request)
