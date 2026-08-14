"""Import smoke test — the package must import cleanly with no env/services.

This is the canary for the rename-failure lesson: importing the OSS package
in a fresh env must never crash or probe services.
"""

from __future__ import annotations

import rugbrain
import rugbrain.providers
import rugbrain.settings


def test_package_imports() -> None:
    assert rugbrain.__version__ == "0.1.0"
    assert isinstance(rugbrain.__all__, list)
    assert "RAGService" in rugbrain.__all__


def test_public_api_not_ported_yet() -> None:
    try:
        _ = rugbrain.RAGService  # noqa: B018 - exercising the stub
    except NotImplementedError:
        pass
    else:  # pragma: no cover - fails once modules are ported
        raise AssertionError("RAGService unexpectedly available — update the test after porting")


def test_providers_are_runtime_checkable() -> None:
    from typing import runtime_checkable

    from rugbrain.providers import ScannerClient

    assert runtime_checkable(ScannerClient) is None or getattr(
        ScannerClient, "_is_runtime_protocol", False
    )
