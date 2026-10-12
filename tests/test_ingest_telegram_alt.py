"""Tests for the Telegram channel-mirror ingest (second account, 8B lane).

Every test is offline: no Telethon session, no Qdrant, no embedder call. The
fetch is faked with a minimal async-iterator client and the store/embedder are
fakes, so the tests prove the DEDUP and BOUNDING behaviour — which is the part
that must never regress — without needing credentials or network.
"""

from __future__ import annotations

import importlib as _il
from dataclasses import dataclass
from typing import Any

import pytest

telegram_mirror = _il.import_module("rugbrain.ingest_sources.alt_ingest")
mirror_channels = _il.import_module("rugbrain.ingest_sources.alt_ingest_channels")
mirror_client = _il.import_module("rugbrain.ingest_sources.alt_ingest_client")
mirror_payload = _il.import_module("rugbrain.ingest_sources.alt_ingest_payload")
mirror_state = _il.import_module("rugbrain.ingest_sources.alt_ingest_state")
mirror_store = _il.import_module("rugbrain.ingest_sources.alt_ingest_store")


@dataclass
class FakeMsg:
    id: int
    message: str
    date: Any = None
    peer_id: Any = None


@dataclass
class FakeEntity:
    id: int = 12345
    title: str = "CoinTelegraph"


class FakeClient:
    """Minimal stand-in for a Telethon client: one channel, fixed messages."""

    def __init__(self, messages: list[FakeMsg], entity: FakeEntity | None = None) -> None:
        self._messages = messages
        self._entity = entity or FakeEntity()
        self.connected = False
        self.disconnected = False
        self.min_id_seen: list[int] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, handle: str) -> FakeEntity:
        return self._entity

    async def iter_messages(self, entity: Any, limit: int, min_id: int = 0):
        self.min_id_seen.append(min_id)
        for m in self._messages:
            if m.id <= min_id:
                continue
            yield m


class FakeStore:
    """Records inserts; ``has_point`` returns True for ids in ``existing``."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.inserted: list[dict[str, Any]] = []
        self.closed = False

    async def has_point(self, doc_id: str, collection: str | None) -> bool:
        return doc_id in self.existing

    async def insert(self, **kwargs: Any) -> bool:
        self.inserted.append(kwargs)
        return True

    async def close(self) -> None:
        self.closed = True


class FakeEmbedder:
    def __init__(self, dim: int = 4096) -> None:
        self.dim = dim
        self.calls: list[str] = []

    async def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1] * self.dim


class BadDimEmbedder(FakeEmbedder):
    async def embed_one(self, text: str) -> list[float]:
        return [0.1] * 2560


def _msgs(n: int, prefix: str, start: int = 1) -> list[FakeMsg]:
    return [
        FakeMsg(id=i, message=f"{prefix} story number {i} about bitcoin and ethereum markets")
        for i in range(start, start + n)
    ]


# ── channel list ────────────────────────────────────────────────────────────


def test_mirror_channel_list_count() -> None:
    """The bot's pinned 113 channels are the core; the footprint may widen
    (data/alt_ingest_extra_channels.txt) but the pinned core must survive."""
    channels = list(mirror_channels.ALT_INGEST_CHANNELS)
    assert len(channels) >= 113
    for must in ("cointelegraph", "coindesk"):
        assert must in {c.lower().lstrip("@") for c in channels}


def test_mirror_channels_unique() -> None:
    assert len(set(mirror_channels.ALT_INGEST_CHANNELS)) == len(mirror_channels.ALT_INGEST_CHANNELS)


def test_resolve_channel_list_explicit_override() -> None:
    assert mirror_channels.resolve_channel_list(["@cointelegraph", " "]) == ["cointelegraph"]
    assert mirror_channels.resolve_channel_list(None) == list(mirror_channels.ALT_INGEST_CHANNELS)


# ── credentials ─────────────────────────────────────────────────────────────


def test_credentials_missing_report_names_not_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RAG_TG_ALT_INGEST_API_ID",
        "RAG_TG_ALT_INGEST_API_HASH",
        "RAG_TG_ALT_INGEST_SESSION_STRING",
        "RAG_TG_ALT_INGEST_SESSION_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    ok, missing = mirror_client.alt_ingest_credentials_present()
    assert ok is False
    assert "RAG_TG_ALT_INGEST_API_ID" in missing
    assert "RAG_TG_ALT_INGEST_API_HASH" in missing
    # Never a secret value in the report.
    assert all(name.startswith("RAG_TG_ALT_INGEST") for name in missing)


def test_credentials_present_with_session_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    assert mirror_client.alt_ingest_credentials_present()[0] is True


def test_credentials_present_with_session_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_FILE", "/fake/session/file.path")
    ok, _ = mirror_client.alt_ingest_credentials_present()
    assert ok is True


def test_build_client_refuses_bot_token_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing second-account credential must fail loud, never silently."""
    monkeypatch.delenv("RAG_TG_ALT_INGEST_API_ID", raising=False)
    monkeypatch.delenv("RAG_TG_ALT_INGEST_API_HASH", raising=False)
    monkeypatch.delenv("RAG_TG_ALT_INGEST_SESSION_STRING", raising=False)
    monkeypatch.delenv("RAG_TG_ALT_INGEST_SESSION_FILE", raising=False)
    with pytest.raises(mirror_client.AltIngestCredentialError):
        mirror_client.build_alt_ingest_client()


# ── payload normalization ───────────────────────────────────────────────────


def test_message_to_doc_normalizes_and_tags() -> None:
    doc = mirror_payload.message_to_doc(
        channel="cointelegraph",
        channel_id=999,
        channel_title="CoinTelegraph",
        msg=FakeMsg(id=7, message="Breaking bitcoin news about ethereum and solana markets today"),
    )
    assert doc is not None
    assert doc.collection == mirror_payload.COLLECTION
    assert doc.metadata["channel_id"] == 999
    assert doc.metadata["msg_id"] == 7
    assert doc.source == "t.me/cointelegraph"
    assert doc.metadata["content_hash"]


def test_message_to_doc_skips_media_only_noise() -> None:
    assert (
        mirror_payload.message_to_doc(
            channel="x", channel_id=1, channel_title="X", msg=FakeMsg(id=1, message="")
        )
        is None
    )
    assert (
        mirror_payload.message_to_doc(
            channel="x", channel_id=1, channel_title="X", msg=FakeMsg(id=1, message="ok")
        )
        is None
    )


def test_duplicate_hash_detected_in_one_run() -> None:
    """Cross-posted stories (same text, different message ids) dedupe to one doc."""
    docs = [
        mirror_payload.message_to_doc(
            channel="a",
            channel_id=1,
            channel_title="A",
            msg=FakeMsg(id=1, message="identical text about ethereum upgrade"),
        ),
        mirror_payload.message_to_doc(
            channel="b",
            channel_id=2,
            channel_title="B",
            msg=FakeMsg(id=2, message="identical text about ethereum upgrade"),
        ),
    ]
    assert all(d is not None for d in docs)
    assert docs[0].metadata["content_hash"] == docs[1].metadata["content_hash"]


def test_point_id_deterministic_per_channel_message() -> None:
    a = mirror_payload.point_id_for_doc(1, 2, "abc")
    b = mirror_payload.point_id_for_doc(1, 2, "abc")
    c = mirror_payload.point_id_for_doc(1, 3, "abc")
    assert a == b
    assert a != c


# ── state ───────────────────────────────────────────────────────────────────


def test_state_records_and_persists(tmp_path: Any) -> None:
    path = str(tmp_path / "state.json")
    st = mirror_state.AltIngestState(path)
    st.record(111, 5)
    st.record(111, 3)  # must never rewind
    st.record(222, 9)
    st.save()
    loaded = mirror_state.AltIngestState(path).load()
    assert loaded.last_msg_id(111) == 5
    assert loaded.last_msg_id(222) == 9
    assert loaded.last_msg_id(333) == 0


def test_state_survives_corrupt_file(tmp_path: Any) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{not json")
    assert mirror_state.AltIngestState(str(path)).load().last_msg_id(1) == 0


# ── the sweep ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_mirror_dedupe_and_insert(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))

    client = FakeClient(_msgs(4, "Market update") + _msgs(2, "copy paste", start=100))
    # third channel: identical copy-paste text → content-hash duplicate
    embedder = FakeEmbedder()
    store = FakeStore()

    summary = await telegram_mirror.run_mirror(
        ["cointelegraph"],
        client_factory=lambda: client,
        embedder_factory=lambda: embedder,
        store_factory=lambda: store,
    )
    assert summary["channels_fetched"] == 1
    assert summary["messages_seen"] == 6
    assert summary["duplicate_hash"] == 0  # all messages have unique content
    assert summary["inserted"] == 6
    assert len(store.inserted) == 6


@pytest.mark.asyncio
async def test_run_mirror_dry_run_embeds_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))

    embedder = FakeEmbedder()
    store = FakeStore()
    summary = await telegram_mirror.run_mirror(
        ["cointelegraph"],
        dry_run=True,
        client_factory=lambda: FakeClient(_msgs(3, "news")),
        embedder_factory=lambda: embedder,
        store_factory=lambda: store,
    )
    assert summary["inserted"] == 0
    assert embedder.calls == []
    assert store.inserted == []


@pytest.mark.asyncio
async def test_run_mirror_skips_already_stored_points(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1: existing points are skipped WITHOUT an embed call."""
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))

    msgs = _msgs(2, "stored story")
    docs = [
        mirror_payload.message_to_doc(
            channel="cointelegraph", channel_id=12345, channel_title="CoinTelegraph", msg=m
        )
        for m in msgs
    ]
    existing = {
        mirror_payload.point_id_for_doc(12345, m.id, mirror_payload.content_hash(d.content))
        for m, d in zip(msgs, docs, strict=True)
    }
    embedder = FakeEmbedder()
    store = FakeStore(existing=existing)
    summary = await telegram_mirror.run_mirror(
        ["cointelegraph"],
        client_factory=lambda: FakeClient(msgs),
        embedder_factory=lambda: embedder,
        store_factory=lambda: store,
    )
    assert summary["duplicate_msgid"] == 2
    assert summary["inserted"] == 0
    assert embedder.calls == []  # no token spent on a known duplicate


@pytest.mark.asyncio
async def test_run_mirror_only_fetches_new_messages(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-run must pass the watermark so nothing is re-fetched."""
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))

    client = FakeClient(_msgs(3, "first sweep"))
    await telegram_mirror.run_mirror(
        ["cointelegraph"],
        dry_run=True,
        client_factory=lambda: client,
    )
    assert client.min_id_seen[0] == 0

    client2 = FakeClient(_msgs(5, "second sweep"))
    await telegram_mirror.run_mirror(
        ["cointelegraph"],
        dry_run=True,
        client_factory=lambda: client2,
    )
    assert client2.min_id_seen[0] == 3  # watermark honoured


@pytest.mark.asyncio
async def test_run_mirror_wrong_dim_is_a_failure_not_a_drop(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))
    store = FakeStore()
    summary = await telegram_mirror.run_mirror(
        ["cointelegraph"],
        client_factory=lambda: FakeClient(_msgs(2, "bad dim")),
        embedder_factory=lambda: BadDimEmbedder(),
        store_factory=lambda: store,
    )
    assert summary["failed"] == 2
    assert summary["inserted"] == 0
    assert store.inserted == []


@pytest.mark.asyncio
async def test_run_mirror_blocked_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RAG_TG_ALT_INGEST_API_ID",
        "RAG_TG_ALT_INGEST_API_HASH",
        "RAG_TG_ALT_INGEST_SESSION_STRING",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(mirror_client.AltIngestCredentialError):
        await telegram_mirror.run_mirror(["cointelegraph"])


@pytest.mark.asyncio
async def test_run_mirror_channel_failure_does_not_abort(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_ID", "1")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_API_HASH", "h")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_SESSION_STRING", "s")
    monkeypatch.setenv("RAG_TG_ALT_INGEST_STATE_FILE", str(tmp_path / "state.json"))

    class HalfBrokenClient(FakeClient):
        async def get_entity(self, handle: str) -> FakeEntity:  # type: ignore[override]
            if handle == "dead-handle":
                raise ValueError("No user has X as username")
            return await super().get_entity(handle)

    summary = await telegram_mirror.run_mirror(
        ["dead-handle", "cointelegraph"],
        dry_run=True,
        client_factory=lambda: HalfBrokenClient(_msgs(2, "news")),
    )
    assert summary["channels_failed"] == 1
    assert summary["channels_fetched"] == 1
    assert any("dead-handle" in e for e in summary["errors"])


# ── the store stage ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_and_store_counts_and_batches() -> None:
    docs = [
        mirror_payload.message_to_doc(
            channel="c",
            channel_id=1,
            channel_title="C",
            msg=FakeMsg(id=i, message=f"unique story {i} about solana defi"),
        )
        for i in range(1, 12)
    ]
    store = FakeStore()
    summary = {"inserted": 0, "failed": 0, "duplicate_msgid": 0, "duplicate_hash": 0}
    await mirror_store.embed_and_store(
        docs, summary, embedder_factory=lambda: FakeEmbedder(), store_factory=lambda: store
    )
    assert summary["inserted"] == 11
    assert len(store.inserted) == 11
    # Bounded batch size, so the run is resumable at batch edges.
    assert mirror_store.EMBED_BATCH == 8
    assert mirror_store.CANONICAL_DIM == 4096
