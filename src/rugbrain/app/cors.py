"""CORS middleware construction.

One job: turn ``CORS_ORIGINS`` into the kwargs for ``CORSMiddleware``.
Secure by default — only ``*.rugmunch.io`` gets the credentialed regex.
"""

from __future__ import annotations

from typing import Any

from fastapi.middleware.cors import CORSMiddleware

from rugbrain.app.config import CORS_ORIGINS

ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Request-ID",
    "X-Payment",
    "X-API-Key",
    "X-CSRF-Token",
]


def cors_kwargs() -> dict[str, Any]:
    """Return the keyword arguments for ``app.add_middleware(CORSMiddleware)``."""
    return {
        "allow_origins": CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else ["*"],
        "allow_origin_regex": r"https?://.*\.rugmunch\.io",
        "allow_credentials": True,
        "allow_methods": ALLOW_METHODS,
        "allow_headers": ALLOW_HEADERS,
    }


__all__ = ["CORSMiddleware", "cors_kwargs"]
