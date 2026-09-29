"""Embed-input derivation and the DeepInfra contract.

The two properties that keep a wrong-width or unsearchable vector out of a
production collection:

  * embed input is the payload's OWN text field when there is one, and is
    deterministically reconstructed AND LABELLED when there is none (that is
    scan_intel: 52k points carrying no text at all, whose original embed
    input is not recoverable);
  * a response of the wrong width aborts instead of being written — the
    4B/2560-into-4096 mistake is the project's defining repeat, and the
    cheap place to stop it is here.

No network: DeepInfra is faked with httpx.MockTransport, so these tests cost
nothing and cannot spend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

_PKG = Path(__file__).resolve().parents[2] / "scripts"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from eliminate_4b.embed import embed_batch, embed_text  # noqa: E402
from eliminate_4b.qdrant_io import Abort  # noqa: E402


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── embed input derivation ─────────────────────────────────────────────
def test_native_text_field_is_used_verbatim() -> None:
    text, source = embed_text({"content": "the real text"})
    assert text == "the real text" and source == "native"


def test_empty_content_falls_through_to_next_field() -> None:
    text, source = embed_text({"content": "   ", "text": "real"})
    assert text == "real" and source == "native"


def test_textless_payload_is_reconstructed_and_labelled() -> None:
    """scan_intel: no text field. Reconstruction is deterministic and says so."""
    pl = {
        "chain": "solana",
        "address": "abc",
        "risk_level": "high",
        "risk_score": 61,
        "risk_flags": ["CR01"],
    }
    a, sa = embed_text(pl)
    b, sb = embed_text(dict(pl))
    assert a == b and sa == sb == "reconstructed"
    assert "solana" in a and "CR01" in a
    assert "text_source" not in pl, "derivation must not mutate the caller's payload"


def test_unusable_payload_yields_no_text() -> None:
    assert embed_text({"nothing": "useful"})[1] == "empty"


def test_empty_payload_yields_no_text() -> None:
    assert embed_text({})[1] == "empty"


# ── DeepInfra contract ──────────────────────────────────────────────────
def test_wrong_dimension_from_provider_aborts() -> None:
    """A 2560 answer to a 4096 collection must abort, never be written."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 2560}]})

    with pytest.raises(Abort, match="4096"):
        embed_batch(_client(handler), ["text"], "key")


def test_batch_response_order_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [1.0] * 4096},
                    {"index": 0, "embedding": [0.0] * 4096},
                ]
            },
        )

    got = embed_batch(_client(handler), ["a", "b"], "key")
    assert got[0] == [0.0] * 4096 and got[1] == [1.0] * 4096


def test_short_provider_response_aborts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 4096}]})

    with pytest.raises(Abort):
        embed_batch(_client(handler), ["a", "b"], "key")


def test_http_error_aborts_without_leaking_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    with pytest.raises(Abort) as exc:
        embed_batch(_client(handler), ["a"], "super-secret-key")
    assert "super-secret-key" not in str(exc.value)
