import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "intel_apps"


@pytest.fixture
def fixture():
    def _load(name: str):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return _load


@pytest.fixture
def recorder():
    """Fake invoke that records calls and returns a canned value."""

    def _make(value=None, *, seconds: float = 0.0, raises: Exception | None = None):
        calls: list[tuple[str, dict]] = []

        async def _invoke(tool: str, args: dict):
            calls.append((tool, args))
            if seconds:
                import asyncio

                await asyncio.sleep(seconds)
            if raises is not None:
                raise raises
            return value

        _invoke.calls = calls  # type: ignore[attr-defined]
        return _invoke

    return _make
