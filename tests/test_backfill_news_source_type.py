"""Provenance rules for ``news_items.source_type`` (no DB, no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "backfill_news_source_type",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_news_source_type.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(mod)


def row(**kw: str) -> dict[str, str]:
    base = {"news_id": "cryptopanic:abc", "url": "https://example.com/a",
            "source": "CoinTelegraph", "body_source": "publisher"}
    base.update(kw)
    return base


def test_telegram_wins_over_quoted_x_link() -> None:
    # A channel post that quotes an x.com link is still a Telegram post.
    assert mod.classify_row(row(news_id="tg:69eaff", url="https://x.com/someone/status/1")) == "telegram"


def test_x_by_id_and_by_url() -> None:
    assert mod.classify_row(row(news_id="x:123", url="https://x.com/a/status/123")) == "x"
    assert mod.classify_row(row(news_id="weird:1", url="https://twitter.com/a")) == "x"


def test_telegram_by_body_source_and_url() -> None:
    assert mod.classify_row(row(body_source="tg:PANewsOfficial")) == "telegram"
    assert mod.classify_row(row(body_source="tg_preview")) == "telegram"
    assert mod.classify_row(row(url="https://t.me/chan/1")) == "telegram"


def test_scraped_and_foreign_namespaces_are_web() -> None:
    assert mod.classify_row(row(news_id="scraped:abc", source="gate-io")) == "web"
    assert mod.classify_row(row(news_id="Dose of DeFi:1", source="Dose of DeFi")) == "web"


def test_security_publisher_inside_rss_namespace() -> None:
    assert mod.classify_row(row(source="The Hacker News")) == "security"
    assert mod.classify_row(row(source="BleepingComputer")) == "security"


def test_general_tech_desk_stays_rss() -> None:
    # Wired/Ars are tech desks, not a security feed.
    assert mod.classify_row(row(source="Wired")) == "rss"


def test_default_is_rss() -> None:
    assert mod.classify_row(row()) == "rss"
