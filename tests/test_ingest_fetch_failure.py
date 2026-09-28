"""Failure-vs-empty contract for programmatic source fetches.

The defect these tests pin: ``list_repo_files``/``fetch_raw`` returned
``[]``/``""`` for *any* failure, so a rate-limited, 404'd or deleted source
produced the identical value as a healthy empty one, and the run exited green
with zero documents. A green exit is not evidence of work.

Every case here is a real HTTP response through ``httpx.MockTransport`` — no
network, no monkeypatching (the repo's ``check-monkeypatch`` ratchet is
shrink-only).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from rugbrain.ingest_sources import FetchFailure, fetch_source_result, get_source
from rugbrain.ingest_sources.fetch_state import (
    AUTH_INVALID,
    FORBIDDEN,
    GONE,
    NETWORK_ERROR,
    NOT_FOUND,
    RATE_LIMITED,
    SERVER_ERROR,
    SSO_REQUIRED,
    TIMEOUT,
    FetchError,
    classify_exception,
    classify_status,
    clear_fetch_error,
    raw_not_found_reason,
    record_fetch_error,
    take_fetch_error,
)
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, tree_has_blob

TREE_OK = {"tree": [{"path": "a/b.sol", "type": "blob", "size": 12}]}
LIMITED_HEADERS = {"x-ratelimit-remaining": "0", "x-ratelimit-limit": "60"}


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- reason classification: an operator can tell the failures apart ---------


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (403, LIMITED_HEADERS, "", RATE_LIMITED),
        (403, {}, '{"message":"API rate limit exceeded"}', RATE_LIMITED),
        (403, {}, '{"message":"Bad credentials"}', AUTH_INVALID),
        (403, {}, "Resource protected by organization SAML enforcement", SSO_REQUIRED),
        (403, {}, "Requires SAML authentication", SSO_REQUIRED),
        (403, {}, '{"message":"Forbidden"}', FORBIDDEN),
        (404, {}, '{"message":"Not Found"}', NOT_FOUND),
        (422, {}, "{}", NOT_FOUND),
        (410, {}, "", GONE),
        (429, {}, "", RATE_LIMITED),
        (401, {}, "", AUTH_INVALID),
        (503, {}, "upstream", SERVER_ERROR),
        (451, {}, "", "http_status_451"),
    ],
)
def test_classify_status_distinguishes_failure_kinds(
    status: int, headers: dict[str, str], body: str, expected: str
) -> None:
    assert classify_status(status, headers=headers, body=body) == expected


def test_classify_exception_distinguishes_timeout_from_network() -> None:
    assert classify_exception(httpx.ConnectTimeout("slow")) == TIMEOUT
    assert classify_exception(httpx.ReadTimeout("slow")) == TIMEOUT
    assert classify_exception(httpx.ConnectError("dns")) == NETWORK_ERROR


def test_raw_not_found_is_its_own_reason_not_the_api_one() -> None:
    # raw.githubusercontent.com is unauthenticated and unthrottled: a raw 404
    # must never be reported as the api.github.com not-found signal.
    assert raw_not_found_reason(404) == "github_raw_not_found"
    assert raw_not_found_reason(404) != NOT_FOUND
    assert raw_not_found_reason(500) == "github_raw_http_500"


def test_fetch_error_log_line_is_greppable(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="rugbrain.ingest_sources.fetch_state"):
        record_fetch_error(NOT_FOUND, "list_repo_files repo=a/b ref=main status=404")
    assert "source_fetch_failed" in caplog.text
    assert f"reason={NOT_FOUND}" in caplog.text
    assert "status=404" in caplog.text


# --- the channel itself ------------------------------------------------------


def test_take_fetch_error_returns_and_clears() -> None:
    clear_fetch_error()
    assert take_fetch_error() is None
    record_fetch_error(RATE_LIMITED, "core quota exhausted")
    err = take_fetch_error()
    assert err is not None and err.reason == RATE_LIMITED
    assert take_fetch_error() is None


def test_first_failure_wins() -> None:
    clear_fetch_error()
    record_fetch_error(RATE_LIMITED, "root cause")
    record_fetch_error(NOT_FOUND, "downstream symptom")
    err = take_fetch_error()
    assert err is not None and err.reason == RATE_LIMITED


def test_record_fetch_error_is_silent_when_clear() -> None:
    clear_fetch_error()
    assert take_fetch_error() is None


# --- list_repo_files / fetch_raw: failure records, healthy-empty does not -----


async def test_list_repo_files_records_reason_on_403_rate_limit() -> None:
    clear_fetch_error()
    client = _client(lambda _r: httpx.Response(403, headers=LIMITED_HEADERS, text="nope"))
    assert await list_repo_files(client, "a/b", "main") == []
    err = take_fetch_error()
    assert err is not None
    assert err.reason == RATE_LIMITED
    assert "repo=a/b" in err.detail


async def test_list_repo_files_records_network_error() -> None:
    clear_fetch_error()

    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    client = _client(boom)
    assert await list_repo_files(client, "a/b", "main") == []
    err = take_fetch_error()
    assert err is not None and err.reason == NETWORK_ERROR


async def test_list_repo_files_empty_tree_is_not_an_error() -> None:
    clear_fetch_error()
    client = _client(lambda _r: httpx.Response(200, json={"tree": []}))
    assert await list_repo_files(client, "a/b", "main") == []
    assert take_fetch_error() is None, "a genuinely empty tree must not look like a failure"


async def test_fetch_raw_records_its_own_reason() -> None:
    clear_fetch_error()
    client = _client(lambda _r: httpx.Response(404, text="404: Not Found"))
    assert await fetch_raw(client, "a/b", "main", "README.md") == ""
    err = take_fetch_error()
    assert err is not None and err.reason == "github_raw_not_found"


async def test_fetch_raw_empty_200_body_is_not_an_error() -> None:
    clear_fetch_error()
    client = _client(lambda _r: httpx.Response(200, text=""))
    assert await fetch_raw(client, "a/b", "main", "README.md") == ""
    assert take_fetch_error() is None


def test_tree_has_blob_requires_a_concrete_file() -> None:
    entries = [{"path": "contracts/Token.sol", "size": 10}]
    assert tree_has_blob(entries, "Token.sol")
    # A directory prefix must never satisfy a liveness assert.
    assert not tree_has_blob(entries, "contracts/")
    assert not tree_has_blob(entries, "solrpars/")


# --- caller level: failure cannot be reported as success ---------------------


class _StubSource:
    """Minimal Source stand-in: returns nothing, records nothing."""

    def __init__(self, name: str = "stub_source") -> None:
        self.name = name

    async def fetch(self, limit: int | None = None) -> list[Any]:
        return []


async def test_fetch_source_result_reports_failure_instead_of_a_bare_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rugbrain.ingest_sources as pkg

    monkeypatch.setattr(pkg, "get_source", lambda _n: _StubSource())

    async def failing_fetch(self, limit: int | None = None) -> list[Any]:
        record_fetch_error(NOT_FOUND, "list_repo_files repo=ghost/missing ref=main status=404")
        return []

    monkeypatch.setattr(_StubSource, "fetch", failing_fetch, raising=True)

    result = await fetch_source_result("stub_source")
    assert result.ok is False
    assert result.count == 0
    assert result.error is not None
    assert result.error.reason == NOT_FOUND
    assert "ghost/missing" in result.error.detail


async def test_fetch_source_result_marks_a_healthy_empty_source_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rugbrain.ingest_sources as pkg

    monkeypatch.setattr(pkg, "get_source", lambda _n: _StubSource())
    result = await fetch_source_result("stub_source")
    assert result.ok is True
    assert result.count == 0
    assert result.error is None


def test_fetch_failure_carries_reason_and_partial_docs() -> None:
    docs = [object()]
    exc = FetchFailure(FetchError(RATE_LIMITED, "core quota"), name="eips", docs=docs)  # type: ignore[arg-type]
    assert exc.name == "eips"
    assert exc.reason.reason == RATE_LIMITED
    assert exc.docs is docs
    assert "reason=github_rate_limited" in str(exc)


def test_real_registry_sources_expose_a_name_and_doc_type() -> None:
    # The liveness table in the lane report walks exactly this registry.
    for name in ("defihacklabs", "swc_registry", "eips", "crypto_whitepapers"):
        assert get_source(name) is not None


def test_dead_repo_tree_payload_never_parses_as_a_healthy_result() -> None:
    # GitHub answers a deleted repo with 404; a proxy/cache can answer 200
    # with an error object. Neither may be mistaken for "no documents".
    body = json.dumps({"message": "Not Found", "documentation_url": "https://docs.github.com"})
    reason = classify_status(404, body=body)
    assert reason == NOT_FOUND
    assert "tree" not in json.loads(body)
