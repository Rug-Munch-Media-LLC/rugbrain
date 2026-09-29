"""The published search contract must survive a refactor.

The 2026-09-29 split moved ~107 lines of query-parameter declarations from the
handler signature into `SearchParams`, injected via `Depends`. A handler
signature no longer describes what the endpoint accepts, so a test asserting on
`inspect.signature` proves nothing about the published contract — it passed
against a declaration nothing read.

This asserts the contract that actually matters: the rating filter parameters
are present in the OpenAPI schema, which is what a client binds to. That check
is what caught three latent defects when the orphaned `search_pkg` split was
made live (a missing import, a double-passed kwarg, and doubled OpenAPI tags).
"""

from __future__ import annotations

#: The rating-filter parameters, as actually published on GET /search.
RATING_FILTER_PARAMS = ("factuality_filter", "min_source_tier", "require_corroboration")


def _openapi_schema() -> dict:
    """The published spec for the search router, the way a client sees it."""
    from fastapi import FastAPI

    from rugbrain.routes.search import router

    app = FastAPI()
    app.include_router(router)
    return app.openapi()


def test_rating_filters_are_published_in_openapi() -> None:
    schema = _openapi_schema()
    paths = schema.get("paths", {})
    assert paths, "no OpenAPI paths: the router failed to mount"

    search = next((p for name, p in paths.items() if name.rstrip("/").endswith("/search")), None)
    assert search is not None, "no /search path published"
    params = {p.get("name") for op in search.values() for p in op.get("parameters", [])}
    for name in RATING_FILTER_PARAMS:
        assert name in params, f"{name} is no longer published on /search"


def test_no_operation_declares_the_same_parameter_twice() -> None:
    """Doubled decorators once doubled every tag in the document."""
    schema = _openapi_schema()
    for path, ops in schema.get("paths", {}).items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            names = [p.get("name") for p in op.get("parameters", [])]
            duplicates = {n for n in names if names.count(n) > 1}
            assert not duplicates, f"{method.upper()} {path} declares {duplicates} twice"
