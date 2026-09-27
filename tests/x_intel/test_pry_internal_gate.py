"""Pry first-party credential: fresh request accepted, stale/missing rejected.

2026-09-27 breaking change: pryscraper added replay protection to the internal
x402 gate. Callers must now send ``X-Internal-Timestamp`` AND sign it, over the
fingerprint ``METHOD\\npath\\ncaller\\n<timestamp>``. A request without it is
402'd (fail-closed), so the rugbrain Pry lane had to change with the gate.

The gate itself is the REAL pryscraper module, loaded from the sibling checkout
(``_server_gate``) and called through ``internal_request`` — so these tests
prove the two implementations agree instead of re-deriving the expectation
here. If pryscraper is not checked out beside us the tests SKIP loudly rather
than pass vacuously.

Zero ``monkeypatch``: the env the gate reads is scoped by a context manager and
Pry is reached through a ``MockTransport``, so the repo's decrease-only
monkeypatch ratchet does not move.
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from rugbrain.ingest_sources.x_intel import pry_lane
from rugbrain.ingest_sources.x_intel.pry_internal_auth import (
    INTERNAL_SIG_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    INTERNAL_TOKEN_HEADER,
    PRY_SCRAPE_PATH,
    internal_fingerprint,
    pry_internal_headers,
)
from rugbrain.ingest_sources.x_intel.pry_lane import scrape_tweet

_TOKEN = "f" * 64
_CALLER = "127.0.0.1"
_TWEET_URL = "https://x.com/peckshield/status/999"
_MAX_SKEW_S = 300
_ENV_KEYS = ("PRY_INTERNAL_TOKEN", "PRY_INTERNAL_MAX_SKEW_SECONDS")

#: The real gate, not a reimplementation of it.
_PRYSCRAPER_SRC = Path("/srv/work/repos/pryscraper/src")
_PRYSCRAPER_INTERNAL = Path(
    "/srv/work/repos/pryscraper/src/pry/payments/x402_middleware/internal.py"
)


def _server_gate() -> Any:
    """pryscraper's real ``internal_request`` (+ ``internal_signature``).

    Imported as a package, not by file location: pryscraper's internal module
    now has package-level imports of its own, so loading it by path would
    import a half-initialised module and fail on ``No module named 'pry'``.
    Prepending the checkout's ``src`` keeps the client and the server reading
    the SAME source, so the expected signature in every accept-case below is
    computed by pryscraper and the two can never drift.
    """
    if not (_PRYSCRAPER_SRC / "pry" / "payments" / "x402_middleware" / "internal.py").is_file():
        pytest.skip(f"pryscraper checkout not available at {_PRYSCRAPER_SRC}")

    # Register pry's package chain as NAMESPACE stubs whose __path__ points at
    # the checkout. The leaf module then imports normally (so its own
    # package-relative imports resolve) without executing pry's real
    # __init__.py, which drags in the full x402 dependency chain (eth_keys and
    # friends) that rugbrain deliberately does not carry.
    created: list[str] = []
    for name, rel in (
        ("pry", "pry"),
        ("pry.payments", "pry/payments"),
        ("pry.payments.x402_middleware", "pry/payments/x402_middleware"),
    ):
        if name in sys.modules:
            continue
        stub = types.ModuleType(name)
        stub.__path__ = [str(_PRYSCRAPER_SRC / rel)]  # type: ignore[attr-defined]
        sys.modules[name] = stub
        created.append(name)
    # The stubs stay registered for the module's lifetime: internal_request()
    # lazily imports classification at call time, so popping them here would
    # make every accept-case fail at the gate.
    globals().setdefault("_PRY_STUBS", []).extend(created)
    sys.modules.pop("pry.payments.x402_middleware.internal", None)
    return importlib.import_module("pry.payments.x402_middleware.internal")


def _gate_dependencies_present() -> bool:
    """Whether rugbrain's venv can run pryscraper's FULL gate.

    ``internal_signature`` is pure stdlib and always importable, so every
    signature-equality case below is real in any environment. The end-to-end
    accept/reject cases need the whole gate (its lazy imports reach pry's
    settings stack, i.e. pydantic_settings) which rugbrain does not
    deliberately carry. Those are skipped rather than faked.
    """
    try:
        importlib.import_module("pry.payments.x402_middleware.classification")
    except Exception:
        return False
    return True


def _scope(caller: str = _CALLER) -> dict[str, Any]:
    """ASGI scope the server resolves *caller* from (the socket peer)."""
    return {"type": "http", "method": "POST", "path": PRY_SCRAPE_PATH, "client": (caller, 44321)}


def _wire(client_headers: dict[str, str]) -> dict[bytes, bytes]:
    """Client header dict -> the bytes-keyed dict ASGI hands the gate."""
    return {k.lower().encode(): v.encode() for k, v in client_headers.items()}


@contextlib.contextmanager
def _gate_env(skew: int = _MAX_SKEW_S) -> Iterator[None]:
    """Arm the server-side gate for the duration of one check."""
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["PRY_INTERNAL_TOKEN"] = _TOKEN
    os.environ["PRY_INTERNAL_MAX_SKEW_SECONDS"] = str(skew)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ── accepted ────────────────────────────────────────────────────────────────


def test_timestamp_must_be_signed_not_merely_sent() -> None:
    """A signature over a different second than the header carries is refused."""
    gate = _server_gate()
    stamp = int(time.time())
    unbound = {
        INTERNAL_TOKEN_HEADER: _TOKEN,
        # Signed one second earlier than what is transmitted.
        INTERNAL_SIG_HEADER: gate.internal_signature(
            _TOKEN, "POST", PRY_SCRAPE_PATH, _CALLER, stamp - 1
        ),
        INTERNAL_TIMESTAMP_HEADER: str(stamp),
    }

    with _gate_env():
        assert gate.internal_request(_scope(), _wire(unbound)) is False


def test_caller_binding_is_the_socket_peer() -> None:
    """Signing a caller other than the resolved one is refused."""
    gate = _server_gate()
    headers = pry_internal_headers(token=_TOKEN, caller="10.9.9.9", timestamp=int(time.time()))

    with _gate_env():
        assert gate.internal_request(_scope("127.0.0.1"), _wire(headers)) is False


# ── our own side ────────────────────────────────────────────────────────────


def test_fingerprint_is_four_newline_joined_fields() -> None:
    assert internal_fingerprint("post", PRY_SCRAPE_PATH, _CALLER, 1750000000) == (
        f"POST\n{PRY_SCRAPE_PATH}\n{_CALLER}\n1750000000"
    )


def test_no_internal_headers_when_token_unset() -> None:
    """Unset secret -> no internal headers (never sign with an empty key)."""
    with contextlib.suppress(KeyError):
        del os.environ["PRY_INTERNAL_TOKEN"]
    assert pry_internal_headers() == {}
    assert INTERNAL_TOKEN_HEADER not in pry_lane.pry_headers()

