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


def test_fresh_timestamp_is_accepted_by_the_server_gate() -> None:
    if not _gate_dependencies_present():
        pytest.skip("rugbrain venv lacks pryscraper's full gate deps (pydantic_settings)")
    """A fresh, correctly signed request passes the real gate."""
    gate = _server_gate()
    stamp = int(time.time())
    headers = pry_internal_headers(token=_TOKEN, caller=_CALLER, timestamp=stamp)

    assert headers[INTERNAL_TIMESTAMP_HEADER] == str(stamp)
    with _gate_env():
        # Computed by PRYSCRAPER, not by us.
        assert headers[INTERNAL_SIG_HEADER] == gate.internal_signature(
            _TOKEN, "POST", PRY_SCRAPE_PATH, _CALLER, str(stamp)
        )
        assert gate.internal_request(_scope(), _wire(headers)) is True


async def test_scrape_tweet_sends_a_fresh_gate_acceptable_credential() -> None:
    if not _gate_dependencies_present():
        pytest.skip("rugbrain venv lacks pryscraper's full gate deps (pydantic_settings)")
    """The lane itself mints, per request, what the real gate accepts."""
    gate = _server_gate()
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "markdown": (
                    "# Title\n\nexploit drained the bridge for 5M USDC. " * 3
                    + "\n\nx.com/peckshield/status/999\n\n1 reply - 42 likes\n"
                )
            },
        )

    with _gate_env():
        tweet = await scrape_tweet(_TWEET_URL, transport=httpx.MockTransport(handler))
        assert tweet is not None
        assert len(sent) == 1
        headers = sent[0].headers
        assert sent[0].url.path == PRY_SCRAPE_PATH
        assert headers[INTERNAL_TOKEN_HEADER] == _TOKEN
        stamp = int(headers[INTERNAL_TIMESTAMP_HEADER])
        assert abs(int(time.time()) - stamp) <= _MAX_SKEW_S
        assert headers[INTERNAL_SIG_HEADER] == gate.internal_signature(
            _TOKEN, "POST", PRY_SCRAPE_PATH, _CALLER, str(stamp)
        )
        # And that exact on-the-wire request opens the real gate.
        assert gate.internal_request(_scope(), _wire(dict(headers))) is True


# ── rejected ────────────────────────────────────────────────────────────────


def test_stale_timestamp_is_rejected_by_the_server_gate() -> None:
    """Older than the skew window: refused (replay protection)."""
    gate = _server_gate()
    stale = int(time.time()) - (_MAX_SKEW_S + 60)
    headers = pry_internal_headers(token=_TOKEN, caller=_CALLER, timestamp=stale)

    with _gate_env():
        assert gate.internal_request(_scope(), _wire(headers)) is False


def test_missing_timestamp_is_rejected_by_the_server_gate() -> None:
    """The pre-2026-09-27 two-header shape is now a 402, not a free pass."""
    gate = _server_gate()
    legacy = {
        INTERNAL_TOKEN_HEADER: _TOKEN,
        INTERNAL_SIG_HEADER: gate.internal_signature(
            _TOKEN, "POST", PRY_SCRAPE_PATH, _CALLER, int(time.time())
        ),
    }

    with _gate_env():
        assert gate.internal_request(_scope(), _wire(legacy)) is False
