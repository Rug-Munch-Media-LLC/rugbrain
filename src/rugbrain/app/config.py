"""Process configuration, read once from the environment.

One job: turn environment variables into validated module-level values and
warn loudly when the API would be reachable without authentication. No I/O
beyond ``os.getenv``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "").lower() in ("1", "true", "yes")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
RMI_RAG_API_KEY = os.getenv("RMI_RAG_API_KEY", "")
DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("1", "true", "yes")

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")


def warn_on_open_auth() -> None:
    """Log the authentication posture once, at import-adjacent call time."""
    if RMI_RAG_API_KEY:
        return
    if DEV_MODE:
        logger.warning(
            "DEV_MODE=true and RMI_RAG_API_KEY is not set — API endpoints are "
            "accessible WITHOUT authentication (development only)."
        )
    else:
        logger.error(
            "RMI_RAG_API_KEY is not set and DEV_MODE is false — the API will "
            "REFUSE ALL REQUESTS (503). Set RMI_RAG_API_KEY in production or "
            "DEV_MODE=true for local development."
        )
