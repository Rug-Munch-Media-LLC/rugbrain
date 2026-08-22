"""Tests for rugbrain.proxy_utils."""

from __future__ import annotations

import importlib
import os

from rugbrain.proxy_utils import get_proxy_client


class TestProxyUtils:
    def test_client_no_proxy(self) -> None:
        client = get_proxy_client(timeout=10)
        assert client is not None

    def test_proxy_enabled(self) -> None:
        os.environ["PROXY_ENABLED"] = "true"
        os.environ["PROXY_URL"] = "socks5://user:pass@host:1080"
        import rugbrain.proxy_utils as pu

        importlib.reload(pu)
        assert pu._should_use_proxy() is True
        del os.environ["PROXY_ENABLED"]
        del os.environ["PROXY_URL"]

    def test_proxy_disabled(self) -> None:
        os.environ["PROXY_ENABLED"] = "false"
        os.environ["PROXY_URL"] = "socks5://user:pass@host:1080"
        import rugbrain.proxy_utils as pu

        importlib.reload(pu)
        assert pu._should_use_proxy() is False
        del os.environ["PROXY_ENABLED"]
        del os.environ["PROXY_URL"]

    def test_client_with_proxy(self) -> None:
        client = get_proxy_client(timeout=10)
        assert client is not None
