"""Client half of Pry's internal x402 gate — timestamped HMAC credential.

Pry first-party calls skip the x402 payment challenge when the request presents
``X-Internal-Token`` plus a valid HMAC over the request fingerprint
(``pry.payments.x402_middleware.internal`` in pryscraper).

The signed message is, byte for byte::

    <METHOD uppercase>\\n<path>\\n<caller>\\n<timestamp>

Three properties make the handshake correct, and each one is easy to get wrong:

* **Replay protection (2026-09-27).** The unix second is signed AND transmitted in
  ``X-Internal-Timestamp``; Pry refuses anything outside
  ``PRY_INTERNAL_MAX_SKEW_SECONDS`` (default 300) and refuses a signature it has
  already seen. A missing, blank or non-digit timestamp REJECTS — so the
  timestamp header is not optional and omitting it turns every call into a 402.
* **One signature per request.** The signature is bound to the timestamp, and a
  seen signature is refused, so it cannot be cached and replayed: a retry must
  mint a fresh timestamp. Hence :func:`pry_internal_headers` is per-call and a
  caller must never build headers once at import time.
* **``caller`` is server-derived, not chosen.** Pry resolves it from the
  connection — socket peer by default, or the right-most non-trusted,
  non-loopback ``X-Forwarded-For`` hop when the immediate peer is loopback or a
  ``PRY_TRUSTED_PROXIES`` entry. Signing anything else yields 402.

The HMAC is computed here (stdlib only) rather than imported from pryscraper:
rugbrain must not take a hard dependency on the server it calls, and the helper
is deliberately dependency-free on the Pry side precisely so callers can do
this. :func:`caller_hint` documents the address to sign.
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC

from __future__ import annotations

import hashlib
import hmac
import os
import time

#: Default loopback caller: `rmi-rag-x-ingest` runs on the host and dials
#: Pry at 127.0.0.1, so the socket peer — and therefore the signed caller — is
#: loopback. Override for a container / proxied deployment (see caller_hint).
DEFAULT_CALLER: str = os.getenv("PRY_INTERNAL_CALLER", "127.0.0.1").strip() or "127.0.0.1"

#: Signed path. Pry verifies against the ASGI `scope["path"]` — the decoded path
#: with NO query string — so this must be the bare path, never the URL.
PRY_SCRAPE_PATH: str = "/v1/scrape"

INTERNAL_TOKEN_HEADER: str = "X-Internal-Token"  # noqa: S105 - an HTTP header name, not a secret
INTERNAL_SIG_HEADER: str = "X-Internal-Signature"
INTERNAL_TIMESTAMP_HEADER: str = "X-Internal-Timestamp"


def caller_hint() -> str:
    """The caller identity a Pry request from this host will be signed as.

    Pry derives it from the connection, so this is only correct when the request
    leaves this process directly over a socket (no proxy rewriting the peer).
    Behind a loopback or trusted proxy Pry reads ``X-Forwarded-For`` instead —
    set ``PRY_INTERNAL_CALLER`` to the right-most non-trusted hop it will see.
    """
    return DEFAULT_CALLER


def internal_fingerprint(method: str, path: str, caller: str, timestamp: str | int) -> str:
    """The exact string Pry HMACs — mirrors pryscraper ``_fingerprint()``."""
    return f"{method.upper()}\n{path}\n{caller}\n{int(timestamp)}"


def internal_signature(
    token: str, method: str, path: str, caller: str, timestamp: str | int | None = None
) -> str:
    """HMAC-SHA256 hex digest Pry's ``internal_request()`` will accept.

    ``timestamp`` defaults to now for convenience, but the caller must send the
    SAME value in ``X-Internal-Timestamp`` — the signature covers it, so a
    mismatch is a 402.
    """
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    message = internal_fingerprint(method, path, caller, stamp)
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def pry_internal_headers(
    method: str = "POST",
    path: str = PRY_SCRAPE_PATH,
    token: str | None = None,
    caller: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """The three internal headers for one fresh, un-replayed Pry request.

    Returns ``{}`` when ``PRY_INTERNAL_TOKEN`` is unset: the caller then falls
    back to the normal Bearer key and Pry answers 402 if the route is paid,
    which is strictly better than sending a signature built from an empty
    secret.

    Must be called per request — the signature is single-use by design.
    """
    secret = ((os.getenv("PRY_INTERNAL_TOKEN") or "") if token is None else token).strip()
    if not secret:
        return {}
    who = (caller_hint() if caller is None else caller).strip()
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    return {
        INTERNAL_TOKEN_HEADER: secret,
        INTERNAL_SIG_HEADER: internal_signature(secret, method, path, who, stamp),
        INTERNAL_TIMESTAMP_HEADER: str(stamp),
    }


__all__ = [
    "DEFAULT_CALLER",
    "INTERNAL_SIG_HEADER",
    "INTERNAL_TIMESTAMP_HEADER",
    "INTERNAL_TOKEN_HEADER",
    "PRY_SCRAPE_PATH",
    "caller_hint",
    "internal_fingerprint",
    "internal_signature",
    "pry_internal_headers",
]
