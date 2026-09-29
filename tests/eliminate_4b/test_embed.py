"""Embed-input derivation and the DeepInfra contract.

The two properties that keep a wrong-width or unsearchable vector out of a
production collection:

  * embed input is the payload's OWN text field when there is one; when there
    is none it is rebuilt AND sha1-proved against the stored ``content_hash``,
    and when it cannot be proved it is REFUSED — never filled with synthesised
    ``key=value`` prose. That refusal is what keeps scan_intel's 52k points and
    agent_vectors' 182 out of the twin: their original embed input is not
    recoverable, and a vector built from invented filler retrieves nothing.
  * a response of the wrong width OR the wrong COUNT aborts instead of being
    written. Width is the project's defining repeat (the 4B/2560-into-4096
    write). Count is the ``rerank_chunks`` defect class, where a 40-document
    pool came back as 5 and a length guard swallowed it as a no-op.

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

from eliminate_4b.embed import embed_batch, embed_text, hash_of_text
from eliminate_4b.qdrant_io import Abort


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── embed input derivation ─────────────────────────────────────────────
def test_native_text_field_is_used_verbatim() -> None:
    text, source = embed_text({"content": "the real text"})
    assert text == "the real text" and source == "native"


def test_empty_content_falls_through_to_next_field() -> None:
    text, source = embed_text({"content": "   ", "text": "real"})
    assert text == "real" and source == "native"


# ── no synthetic prose: the scan_intel / agent_vectors refusal ─────────
def test_textless_payload_is_refused_not_invented() -> None:
    """scan_intel carries only keys, addresses and scores. No text, no vector."""
    pl = {
        "chain": "solana",
        "address": "abc",
        "risk_level": "high",
        "risk_score": 61,
        "risk_flags": ["CR01"],
    }
    text, source = embed_text(pl)
    assert text == "", "a textless payload must yield no embed input at all"
    assert source == "unrecoverable"
    assert "chain=" not in text and "CR01" not in text, "no key=value filler"


def test_textless_payload_with_no_hash_is_refused() -> None:
    assert embed_text({"nothing": "useful"}) == ("", "unrecoverable")


def test_empty_payload_is_refused() -> None:
    assert embed_text({}) == ("", "unrecoverable")


def test_refusal_is_deterministic_and_non_mutating() -> None:
    pl = {"chain": "solana", "address": "abc", "risk_level": "high"}
    assert embed_text(pl) == embed_text(dict(pl))
    assert pl == {"chain": "solana", "address": "abc", "risk_level": "high"}


# ── derived text must PROVE itself against content_hash ────────────────
def _coded() -> dict:
    """A payload shaped like a live codebase_reference_v2 point."""
    return {
        "library_id": "internal/x",
        "file_path": "a/b.py",
        "function_name": "go",
        "title": "go()",
        "description": "moves",
        "code_block": "def go(): ...",
    }


def test_derived_text_is_used_when_its_hash_proves_it() -> None:
    pl = _coded()
    pl["content_hash"] = hash_of_text("internal/x a/b.py:go\n\ngo()\n\nmoves\n\ndef go(): ...")
    text, source = embed_text(pl)
    assert source == "derived"
    assert text == "internal/x a/b.py:go\n\ngo()\n\nmoves\n\ndef go(): ..."


def test_derived_text_carries_the_qualifier_first() -> None:
    """Not cosmetic: a bare `get` chunk from 4 libraries embeds near-identical
    without it. The ordering is part of the oracle-proven string."""
    pl = _coded()
    pl["content_hash"] = hash_of_text("internal/x a/b.py:go\n\ngo()\n\nmoves\n\ndef go(): ...")
    assert embed_text(pl)[0].startswith("internal/x a/b.py:go\n\n")


def test_qualifier_falls_back_to_title_without_a_function_name() -> None:
    pl = _coded()
    pl["function_name"] = ""
    pl["content_hash"] = hash_of_text("internal/x a/b.py:go()\n\ngo()\n\nmoves\n\ndef go(): ...")
    assert embed_text(pl)[1] == "derived"


def test_derived_text_is_refused_when_the_hash_disagrees() -> None:
    """A near-miss is worse than no vector: it costs a call and retrieves noise.

    This is the 91.3%-on-the-head / 0%-on-the-tail trap: a plausible
    alternative rule hashes equal for one builder and not another, so the
    oracle - not plausibility - has to decide.
    """
    pl = _coded()
    pl["content_hash"] = "0" * 40
    assert embed_text(pl) == ("", "unrecoverable")


def test_blank_fields_keep_their_place_in_the_string() -> None:
    """Collapsing blank parts changes the string and fails the hash, which is
    exactly how a plausible-looking rule scored 0% on the real tail."""
    pl = _coded()
    pl["description"] = "   "
    pl["content_hash"] = hash_of_text("internal/x a/b.py:go\n\ngo()\n\n   \n\ndef go(): ...")
    assert embed_text(pl)[1] == "derived"


def test_native_text_beats_a_mismatched_content_hash() -> None:
    """A real text field needs no proof, and must not be second-guessed."""
    pl = {"content": "real", "content_hash": "0" * 40}
    assert embed_text(pl) == ("real", "native")


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
    """The rerank_chunks defect: 5 results for 40 documents. Must RAISE."""
    pool = [f"doc {i}" for i in range(40)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.0] * 4096} for i in range(5)]}
        )

    with pytest.raises(Abort, match="5 vectors for 40 inputs"):
        embed_batch(_client(handler), pool, "key")


def test_empty_batch_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not call the provider with an empty batch")

    assert embed_batch(_client(handler), [], "key") == []


def test_http_error_aborts_without_leaking_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    with pytest.raises(Abort) as exc:
        embed_batch(_client(handler), ["a"], "super-secret-key")
    assert "super-secret-key" not in str(exc.value)
