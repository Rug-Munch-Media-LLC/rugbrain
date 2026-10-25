"""Pipeline contract: cross-source dedupe, change feed, latency, alerts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from rugbrain.zeroday import alerts
from rugbrain.zeroday.pipeline import Change, merge
from rugbrain.zeroday.schema import Record

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
PUBLISHED = (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")


def _advisory(severity: str = "high") -> Record:
    return Record(
        source="github_advisories",
        kind="0day",
        title="RPC parsing bug",
        url="https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
        published_at=PUBLISHED,
        severity=severity,
        external_ids=("GHSA-aaaa-bbbb-cccc", "CVE-2026-9999"),
    )


def _nvd() -> Record:
    return Record(
        source="nvd",
        kind="0day",
        title="CVE-2026-9999",
        url="https://nvd.nist.gov/vuln/detail/CVE-2026-9999",
        published_at=PUBLISHED,
        severity="high",
        external_ids=("CVE-2026-9999",),
    )


def test_cross_source_dedupe_and_latency() -> None:
    first = merge(None, [_advisory()], now=NOW)
    assert first.latency["clusters"] == 1
    assert first.changes[0].lag_s == 7200

    second = merge(first.state, [_nvd()], now=NOW + timedelta(minutes=5))
    assert second.latency["clusters"] == 1
    assert second.latency["multi_source_clusters"] == 1
    joined = second.changes[0]
    assert joined.cluster_id == "cve:CVE-2026-9999"
    assert joined.cross_source_lag_s == 300


def test_reingest_is_idempotent_and_updates_emit_changes() -> None:
    once = merge(None, [_advisory()], now=NOW)
    again = merge(once.state, [_advisory()], now=NOW)
    assert again.changes == ()

    changed = merge(once.state, [_advisory(severity="critical")], now=NOW)
    assert [c.event for c in changed.changes] == ["updated"]
    assert changed.state["clusters"]["cve:CVE-2026-9999"]["members"]


def test_missing_published_at_has_no_latency() -> None:
    record = Record(
        source="exploit_db",
        kind="exploit",
        title="PoC",
        url="https://www.exploit-db.com/exploits/1",
    )
    outcome = merge(None, [record], now=NOW)
    assert outcome.changes[0].lag_s is None


def test_alert_gate() -> None:
    exploit = Change(
        event="new",
        cluster_id="c1",
        record=Record(
            source="defillama_hacks",
            kind="exploit",
            title="Big hack",
            url="https://x/1",
            severity="critical",
            loss_usd=50_000_000,
        ),
        first_seen="2026-10-25T12:00:00Z",
        lag_s=None,
        cross_source_lag_s=None,
    )
    updated = Change(
        event="updated",
        cluster_id="c1",
        record=exploit.record,
        first_seen="2026-10-25T12:00:00Z",
        lag_s=None,
        cross_source_lag_s=None,
    )
    assert alerts.should_alert(exploit) is True
    assert alerts.should_alert(updated) is False

    bounty = Change(
        event="new",
        cluster_id="c2",
        record=Record(
            source="immunefi",
            kind="bounty",
            title="Program",
            url="https://x/2",
            reward_usd=250_000,
        ),
        first_seen="2026-10-25T12:00:00Z",
        lag_s=None,
        cross_source_lag_s=None,
    )
    small = Change(
        event="new",
        cluster_id="c3",
        record=Record(
            source="immunefi",
            kind="bounty",
            title="Small",
            url="https://x/3",
            reward_usd=1_000,
        ),
        first_seen="2026-10-25T12:00:00Z",
        lag_s=None,
        cross_source_lag_s=None,
    )
    assert alerts.should_alert(bounty) is True
    assert alerts.should_alert(small) is False


def test_alert_delivery_is_once_per_fingerprint(monkeypatch) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_TELEGRAM", "https://telegram.example/bot/sendMessage")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    change = Change(
        event="new",
        cluster_id="c9",
        record=Record(
            source="defillama_hacks",
            kind="exploit",
            title="Critical hack",
            url="https://x/9",
            severity="critical",
            loss_usd=10_000_000,
        ),
        first_seen="2026-10-25T12:00:00Z",
        lag_s=None,
        cross_source_lag_s=None,
    )
    state: dict[str, object] = {}

    async def run() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sent_first = await alerts.send_batch([change], state, client=client)
            sent_second = await alerts.send_batch([change], state, client=client)
        return sent_first + sent_second

    sent = asyncio.run(run())
    assert sent == ["c9"]
    assert len(calls) == 1
