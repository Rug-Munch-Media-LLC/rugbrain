"""The live document shape: what Lane B writes, projected and validated once.

The offline fixture document and the live writer document were two different
shapes, so the real run failed the publication validator with
``schema: missing slug; missing url; missing dek; …`` while the fixture passed.
These tests pin the projection that closes the gap, and the refusal path that
must report thin evidence instead of a schema gap.
"""

from __future__ import annotations

import json
from typing import Any

from rugbrain import content_story
from rugbrain.content import editions as content_editions
from rugbrain.content import writer as content_writer
from rugbrain.content.draft import parse_draft
from rugbrain.content.notes import normalise_notes
from rugbrain.content_daily import adapters
from rugbrain.content_style import load_style

DAY = "2026-10-28"
BASE_URL = "https://rugmunch.io/story/"
EDITION = "morning"

ROWS: list[dict[str, Any]] = [
    {
        "title": "Velodrome router drain",
        "publisher": "Rekt News",
        "url": "https://rekt.news/velodrome-router",
        "content": "An attacker used a stale router approval to drain three pools.",
    },
    {
        "title": "Optimism finality",
        "publisher": "L2Beat",
        "url": "https://l2beat.com/scaling/optimism",
        "content": "Sequencer finality lagged across the same window.",
    },
    {
        "title": "Bridge flow",
        "publisher": "Nansen",
        "url": "https://nansen.ai/research/bridge-flow",
        "content": "Net bridge outflow rose overnight.",
    },
]

PARAGRAPHS: list[str] = [
    "Three Velodrome pools were drained after a stale router approval [1].",
    "Sequencer finality lagged across the same window, and bridge outflow rose [2][3].",
    "The desk's read is that the drain stayed inside three pools across the window [1][2].",
    "The next scheduled item is a supply unlock the desk will mark if the tape moves [3].",
]

RAW = json.dumps(
    {
        "status": "ok",
        "headline": "Velodrome drain and a sequencer stall",
        "dek": "A stale router approval and a sequencer delay landed in the same window.",
        "paragraphs": PARAGRAPHS,
        "telegram_teaser": "Three Velodrome pools drained; Optimism finality lagged.",
    }
)


def live_document(day: str = DAY) -> dict[str, Any]:
    """The exact record ``content.writer`` returns for an accepted draft."""
    spec = content_editions.edition(EDITION)
    rules = load_style()
    rows = normalise_notes(ROWS)
    draft = parse_draft(RAW, spec, rows, day, rules)
    assert draft["status"] == "ok", draft["style_violations"]
    return content_writer._document(
        spec, day, draft, rows, {"status": "no_image", "reason": "test"}, rules
    )


def test_written_document_lacks_the_publication_identity() -> None:
    """Precondition: Lane B's document is not the publication shape."""
    doc = live_document()
    for key in ("slug", "url", "tokens", "charts", "open_graph"):
        assert key not in doc
    assert set(doc["sections"][0]) == {"heading", "paragraph"}


def test_projection_makes_the_live_document_publishable() -> None:
    """One projection closes the shape gap; the real validator must agree."""
    doc = adapters._normalise_written(EDITION, DAY, live_document())
    assert doc["slug"] == f"daily-rug-{DAY}-{EDITION}"
    assert str(doc["url"]).startswith(BASE_URL)
    assert doc["sections"][0]["body"] == PARAGRAPHS[0]
    assert adapters.edit_document(doc) == []


def test_projection_never_overwrites_what_the_writer_set() -> None:
    """Identity fields are pipeline-owned; a value Lane B set is kept."""
    doc = adapters._normalise_written(
        EDITION, DAY, dict(live_document(), slug="my-slug", url="https://rugmunch.io/story/my-slug")
    )
    assert doc["slug"] == "my-slug"
    assert doc["url"] == "https://rugmunch.io/story/my-slug"


def test_writer_refusal_is_reported_by_its_own_reason() -> None:
    """A refusal is thin evidence, not a missing slug — say so, once."""
    refused = {
        "status": "insufficient_evidence",
        "reason": "only 0 citable source(s) with a URL; need 2",
    }
    problems = adapters.edit_document(refused)
    assert problems == [
        "writer: insufficient_evidence: only 0 citable source(s) with a URL; need 2"
    ]
    assert not any("schema:" in item for item in problems)


def test_telegram_row_is_citable_by_its_message_permalink() -> None:
    """A channel handle alone is not a source; the message id makes it one."""
    hit = {
        "id": "point-1",
        "content": "[TG/news] desk: a router was drained",
        "source": "t.me/rmialerts",
        "metadata": {"kind": "telegram_message", "msg_id": 1208},
    }
    assert content_story.normalise(hit, "news_v2")["url"] == "https://t.me/rmialerts/1208"


def test_telegram_display_name_is_not_made_up_into_a_url() -> None:
    """No derivable identity means no citation — never a guessed permalink."""
    hit = {
        "id": "point-2",
        "content": "[TG/news] desk: a router was drained",
        "source": "telegram:Crypto News",
        "metadata": {"kind": "telegram_message", "msg_id": 5},
    }
    assert content_story.normalise(hit, "social_intel")["url"] == ""
