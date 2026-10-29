"""Acceptance harness for the three daily editions — no network, no disk outside tmp.

Every stage either runs for real (dedupe, routing, the publication validator,
the Telegram payload renderer) or is a declared seam replaced by a fixture.
What is asserted is the shipped contract, not the pipeline's internals:

(a) three distinct editions with the exact titles and slots
(b) 2–4 paragraphs per edition, inside the edition's own bounds
(c) a duplicate run publishes nothing and posts nothing a second time
(d) a document with no story image falls back to a text post
(e) the published site URL is present, and it is on the canonical base
(f) the Telegram payload is fully rendered (method, text, url, idempotency)
(g) a dead store isolates to ``site_failed`` and Telegram is never attempted
(h) a Telegram 429 isolates to ``partial``; the site stays published and is
    not rolled back, and the next run does not re-post it
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import rugbrain
from rugbrain.content_daily import cli, delivery, editions, fixtures
from rugbrain.content_daily.journal import Journal
from rugbrain.content_daily.ledger import DailyLedger
from rugbrain.content_daily.pipeline import run_edition

DAY = "2026-10-29"
BASE_URL = "https://rugmunch.io/story/"

# The brand line, typed once, exactly as published. A change here is an
# editorial decision, not a refactor.
TITLES = {
    "morning": "Rug Munch Intelligence — The Morning Rug",
    "midday": "Rug Munch Intelligence — Midday Market Report",
    "evening": "Rug Munch Intelligence — Evening Market Closing Report",
}
SLOTS = {"morning": "06:30", "midday": "12:30", "evening": "21:30"}


def _store(tmp_path: Path, key: str) -> tuple[DailyLedger, Journal]:
    """One ledger per edition: a shared one would suppress across editions."""
    return DailyLedger(tmp_path / key / "ledger.jsonl"), Journal(tmp_path / key / "journal.jsonl")


async def _run(tmp_path: Path, key: str, **overrides: Any) -> dict[str, Any]:
    """One edition against offline dependencies, isolated journal + ledger."""
    ledger, journal = _store(tmp_path, key)
    return await run_edition(
        key,
        day=DAY,
        force=True,
        deps=fixtures.offline_deps(**overrides),
        ledger=ledger,
        journal=journal,
    )


def test_editions_carry_the_exact_titles_and_slots() -> None:
    """(a) the contract table, and the canonical table it must agree with."""
    assert {key: editions.get(key).title_for(DAY) for key in TITLES} == TITLES
    assert {key: editions.get(key).at_utc for key in SLOTS} == SLOTS
    assert len(set(TITLES.values())) == 3


async def test_three_distinct_editions_publish_three_distinct_urls(tmp_path: Path) -> None:
    """(a) + (e): one run each, three titles, three URLs on the canonical base."""
    results = [await _run(tmp_path, key) for key in TITLES]
    assert [row["status"] for row in results] == ["ok", "ok", "ok"]
    assert [row["title"] for row in results] == list(TITLES.values())
    urls = [str(row["url"]) for row in results]
    assert len(set(urls)) == 3, urls
    assert all(url.startswith(BASE_URL) for url in urls), urls


async def test_each_edition_carries_two_to_four_paragraphs(tmp_path: Path) -> None:
    """(b) the document the writer produced, counted where it is produced."""
    written: dict[str, dict[str, Any]] = {}

    async def recording_write(key: str, stories: list[dict[str, Any]], day: str) -> dict[str, Any]:
        written[key] = fixtures.document(key, day)
        return written[key]

    for key in TITLES:
        assert (await _run(tmp_path, key, write=recording_write))["status"] == "ok"
        paragraphs = written[key]["paragraphs"]
        edition = editions.get(key)
        assert 2 <= len(paragraphs) <= 4, (key, len(paragraphs))
        assert edition.min_paragraphs <= len(paragraphs) <= edition.max_paragraphs


async def test_duplicate_run_publishes_and_posts_once(tmp_path: Path) -> None:
    """(c) the ledger suppresses the second run; the pool's copy is collapsed."""
    publishes: list[str] = []

    async def counting_publish(doc: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        publishes.append(str(doc["slug"]))
        return {
            "ok": True,
            "slug": doc["slug"],
            "url": doc["url"],
            "revision": len(publishes),
            "title": doc["edition_title"],
        }

    first = await _run(tmp_path, "morning", publish=counting_publish)
    second = await _run(tmp_path, "morning", publish=counting_publish)

    assert first["status"] == "ok"
    assert first["dedupe_drops"] >= 1, "the duplicate story was not collapsed"
    assert second["status"] == "skipped"
    assert second["reason"] == "already_published"
    assert second["message_ids"] == []
    assert len(publishes) == 1


def test_missing_image_falls_back_to_a_text_post() -> None:
    """(d) no story image → sendMessage; a real image → sendPhoto."""
    doc = fixtures.document("morning", DAY)
    assert delivery.image_for(doc) is None
    text_post = delivery.telegram_payload("morning", doc, {"url": doc["url"], "run_key": "k"})
    assert text_post["method"] == "sendMessage"
    assert text_post["image_url"] is None

    with_image = {**doc, "image_url": "https://cdn.rugmunch.io/img/velodrome.png"}
    photo = delivery.telegram_payload("morning", with_image, {"url": doc["url"], "run_key": "k"})
    assert photo["method"] == "sendPhoto"


async def test_telegram_payload_is_rendered_and_carries_the_url(tmp_path: Path) -> None:
    """(e) + (f): the exact body the channel bridge receives."""
    result = await _run(tmp_path, "midday")
    payload = result["telegram"]
    assert result["status"] == "ok"
    assert result["url"].startswith(BASE_URL)
    assert payload["url"] == result["url"]
    assert result["url"] in payload["text"]
    assert payload["channel"] == editions.get("midday").channel
    assert payload["edition"] == "midday"
    assert payload["run_key"] == result["run_key"]
    assert payload["idempotency_key"] == f"{result['run_key']}:tg:main"
    assert payload["parse_mode"] == "HTML"
    assert payload["method"] in {"sendMessage", "sendPhoto"}
    assert result["message_ids"] == [9001]


async def test_store_down_isolates_to_site_failed(tmp_path: Path) -> None:
    """(g) a dead store is fatal for the run and never reaches Telegram."""
    delivered: list[str] = []

    async def dead_store(doc: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        raise delivery.HttpStatus(503, "ghost unreachable", retry_after=0.0)

    async def recording_deliver(
        key: str, doc: dict[str, Any], published: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        delivered.append(key)
        return {"ok": True, "message_ids": [1], "payload": {}}

    result = await _run(tmp_path, "evening", publish=dead_store, deliver=recording_deliver)

    assert result["status"] == "site_failed"
    assert result["url"] is None
    assert result["message_ids"] == []
    assert delivered == [], "Telegram was attempted despite a failed publication"
    publish_stage = next(row for row in result["stages"] if row["name"] == "publish_site")
    assert publish_stage["attempts"] == 3
    assert publish_stage["error_class"] == "http_5xx"
    assert cli.exit_code(result) == cli.EXIT_SITE_FAILED


async def test_telegram_429_isolates_to_partial_without_rollback(tmp_path: Path) -> None:
    """(h) the page stays published; the channel miss is reported, not retried forever."""
    attempts: list[str] = []

    async def throttled(
        key: str, doc: dict[str, Any], published: dict[str, Any], dry_run: bool
    ) -> Any:
        attempts.append(key)
        raise delivery.HttpStatus(429, "flood control", retry_after=0.0)

    first = await _run(tmp_path, "evening", deliver=throttled)
    assert first["status"] == "partial"
    assert str(first["url"]).startswith(BASE_URL)
    assert first["message_ids"] == []
    assert len(attempts) == 3, "the 429 did not use the declared three attempts"
    assert cli.exit_code(first) == cli.EXIT_PARTIAL

    second = await _run(tmp_path, "evening")
    assert second["status"] == "skipped"
    assert second["reason"] == "already_published"
    assert second["url"] == first["url"], "the published page was not rolled back, nor re-minted"


def test_cli_offline_run_prints_the_envelope_and_exits_zero(tmp_path: Path) -> None:
    """The exact command the systemd unit runs, end to end."""
    repo = Path(rugbrain.__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(repo / "src"),
        "RUGBRAIN_CONTENT_LOG": str(tmp_path / "journal.jsonl"),
        "RUGBRAIN_CONTENT_LEDGER": str(tmp_path / "ledger.jsonl"),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "rugbrain.content_daily",
            "daily",
            "--edition",
            "morning",
            "--date",
            DAY,
            "--offline",
            "--force",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["title"] == TITLES["morning"]
    assert payload["url"].startswith(BASE_URL)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("ok", 0),
        ("repaired", 0),
        ("skipped", 0),
        ("no_candidates", 2),
        ("needs_review", 2),
        ("not_due", 2),
        ("site_failed", 3),
        ("partial", 4),
        ("something_new", 1),
    ],
)
def test_exit_code_contract(status: str, code: int) -> None:
    """A failed run must never exit 0, and an unknown status is never silent."""
    assert cli.exit_code({"status": status}) == code
