"""Credential headers built in exactly one place.

One job — turn a token into the ``Authorization`` header, or into no header
at all when the token is absent. Sources never format the scheme themselves.
"""

from __future__ import annotations


def bearer(token: str | None, *, scheme: str = "Bearer") -> dict[str, str]:
    """``{"Authorization": "<scheme> <token>"}``, or ``{}`` without a token."""
    token = (token or "").strip()
    return {"Authorization": f"{scheme} {token}"} if token else {}


__all__ = ["bearer"]
