"""Discovery from the account's own dialogs: classify, append, dedupe.

Everything here is offline. The discovery path's whole claim is that it adds
nothing it has not verified, so the tests pin the three failure modes that
would break that claim: a news channel scored as a scam call-out, a duplicate
handle entering the sweep list twice, and the same shilled text costing more
than one embed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.ingest_sources import ingest_cycle
from rugbrain.ingest_sources.channel_registry import append_channels, normalise, read_channels
from rugbrain.ingest_sources.discover_joined_channels import dialogs_from_inventory
from rugbrain.ingest_sources.joined_classifier import classify_joined
from rugbrain.ingest_sources.scam_channels_pinned import ACTIVE, ALL_VERIFIED, DORMANT


@pytest.mark.parametrize(
    ("title", "handle", "expected"),
    [
        ("Catfish Calls by Poe", "CatfishcallsbyPoe", "shiller"),
        ("Milady Calls", "miladydegencalls", "shiller"),
        ("Scam Alert Official", "scamalertsofficial", "scam_caller"),
        ("$WAP - Portal", "wapcoinwtf", "promotion"),
        ("Traffic Ad", "trafficad", "promotion"),
        ("Crypto Rug Munchers | RMI Chat", "crmunchers", None),
        ("RMI Security — Rug Alerts & Exploits", "rmialerts", None),
        ("Binance Announcements", "binance_announcements", None),
        ("CryptoRank news", "cryptorank_news", None),
        ("MonadBlockChain", "monadblockchain", None),
    ],
)
def test_classify_joined(title: str, handle: str, expected: str | None) -> None:
    """A news outlet is not a scam call-out, and our own channel is neither."""
    assert classify_joined(title, handle) == expected


def test_classify_joined_ignores_case_and_at_prefix() -> None:
    assert classify_joined("Milady Calls", "@MiladyDegenCalls") == "shiller"


def test_append_channels_dedupes_case_and_at_prefix(tmp_path: Path) -> None:
    """A re-run must be a no-op — that is what makes the CLI safe to re-run."""
    channels = tmp_path / "tg_channels.txt"
    channels.write_text("cointelegraph\n@WatcherGuru\n")

    added = append_channels(["watcherguru", "@ClearlyDegen", "clearlydegen"], channels)

    assert added == ["ClearlyDegen"]
    assert read_channels(channels) == ["cointelegraph", "WatcherGuru", "ClearlyDegen"]
    assert append_channels(["WatcherGuru", "clearlydegen"], channels) == []


def test_append_channels_creates_missing_file(tmp_path: Path) -> None:
    channels = tmp_path / "nested" / "tg_channels.txt"
    assert append_channels(["soltrenchtrending"], channels) == ["soltrenchtrending"]
    assert read_channels(channels) == ["soltrenchtrending"]


def test_normalise_strips_at_and_space() -> None:
    assert normalise("  @WatcherGuru ") == "WatcherGuru"


def test_dialogs_from_inventory_skips_private_and_own(tmp_path: Path) -> None:
    """Private groups cannot be read accountlessly, so they must not be registered."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "inventory": [
                    {"title": "Milady Calls", "username": "miladydegencalls"},
                    {"title": "Borgy - CRM", "username": ""},
                    {"title": "Crypto Rug Munchers", "username": "crmunchers"},
                    {"title": "CryptoRank news", "username": "cryptorank_news"},
                ]
            }
        )
    )

    dialogs = dialogs_from_inventory(str(state))

    assert [(d.username, d.category) for d in dialogs] == [
        ("miladydegencalls", "shiller"),
        ("crmunchers", None),
        ("cryptorank_news", None),
    ]


def test_dialogs_from_inventory_survives_missing_file(tmp_path: Path) -> None:
    assert dialogs_from_inventory(str(tmp_path / "absent.json")) == []


def test_gate_and_bound_collapses_mirrored_text() -> None:
    """One shilled text in five channels is one embed, not five."""
    kwargs = {
        "get_text": lambda i: i["t"],
        "get_category": lambda i: "crypto",
        "get_hash": lambda i: i["h"],
    }
    mirrored = [{"t": "buy $X now", "h": "same"} for _ in range(5)] + [{"t": "other", "h": "b"}]

    fresh, ring, stats = ingest_cycle.gate_and_bound(mirrored, seen_hashes=[], **kwargs)  # type: ignore[arg-type]

    assert [i["h"] for i in fresh] == ["same", "b"]
    assert stats["cross_channel_duplicates"] == 4
    assert ring == ["same", "b"]


def test_pinned_list_is_unique_and_verified_shape() -> None:
    """The pinned provenance may never carry the same handle twice."""
    handles = [h.lower() for h, _ in ALL_VERIFIED]
    assert len(handles) == len(set(handles))
    assert ALL_VERIFIED == ACTIVE + DORMANT
    assert {cat for _, cat in ALL_VERIFIED} <= {"scam_caller", "shiller", "security", "promotion"}
    # Own-brand channels must never be pinned as scam intel.
    assert not [
        h for h in handles if h.startswith(("cryptorugmunch", "rmialert", "rmialpha", "munchscan"))
    ]
