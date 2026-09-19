"""HMAC-SHA256 signing + constant-time verification for webhook payloads.

``sign_payload``/``verify_signature`` are a single signing contract
(sign on the way out, verify on the way in) and are kept together.
"""

from __future__ import annotations

import hashlib
import hmac


def sign_payload(secret: str, payload: bytes | str) -> str:
    """Return ``sha256=<hex>`` HMAC-SHA256 signature of *payload*."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    digest = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, signature: str, payload: bytes | str) -> bool:
    """Constant-time check that *signature* matches the HMAC of *payload*."""
    expected = sign_payload(secret, payload)
    return hmac.compare_digest(expected, signature or "")
