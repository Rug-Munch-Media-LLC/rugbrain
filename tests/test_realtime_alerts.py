"""Tests for rugbrain.realtime_alerts — real-time exploit detection.

Covers exploit heuristics, model determinism, HMAC signing, the webhook
registry + signed delivery, SearXNG polling (RSS + JSON), ClickHouse
on-chain query construction, RPC large-tx filtering, dedup, and the
monitor pipeline (all external services mocked).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

import rugbrain.realtime_alerts as ra

# ═══════════════════════════════════════════════════════════════════════
# Model + heuristics
# ═══════════════════════════════════════════════════════════════════════


def test_critical_alert_id_deterministic() -> None:
    a = ra.CriticalAlert(title="Curve Hack", source="test", detected_at="2026-08-04T00:00:00+00:00")
    b = ra.CriticalAlert(title="Curve Hack", source="test", detected_at="2026-08-04T00:00:00+00:00")
    assert a.id == b.id
    c = ra.CriticalAlert(title="Curve Hack", source="test", detected_at="2026-08-05T00:00:00+00:00")
    assert a.id != c.id


def test_critical_alert_from_candidate_classifies() -> None:
    a = ra.CriticalAlert.from_candidate(
        title="Oracle price manipulation exploit drains $4.2M from Solana protocol",
        description="Attacker manipulated the price feed and drained funds.",
    )
    assert a.chain == "solana"
    assert a.amount_usd == pytest.approx(4_200_000, rel=1e-3)
    assert a.id


def test_critical_alert_from_candidate_unknown() -> None:
    a = ra.CriticalAlert.from_candidate(title="Just a market update")
    assert a.chain == "unknown"
    assert a.amount_usd == 0.0


def test_is_exploit_candidate() -> None:
    assert ra.is_exploit_candidate("Exploit detected on Ethereum mainnet")
    assert ra.is_exploit_candidate("funds drained from bridge")
    assert ra.is_exploit_candidate("critical vulnerability disclosed")
    assert not ra.is_exploit_candidate("market making is fine today")


def test_parse_amount_usd_formats() -> None:
    assert ra.parse_amount_usd("lost $1.2M") == pytest.approx(1_200_000)
    assert ra.parse_amount_usd("stolen $4,800,000") == pytest.approx(4_800_000)
    assert ra.parse_amount_usd("$300k total") == pytest.approx(300_000)
    assert ra.parse_amount_usd("no money mentioned") == 0.0


def test_detect_chain_critical() -> None:
    assert ra.detect_chain_critical("exploit on solana") == "solana"
    assert ra.detect_chain_critical("ethereum mainnet hack") == "ethereum"
    assert ra.detect_chain_critical("cross-chain bridge") == "multi"
    assert ra.detect_chain_critical("nothing") == "unknown"


def test_text_contains_fields() -> None:
    a = ra.CriticalAlert(title="Hack", description="Drained", amount_usd=1_000_000)
    text = a.text()
    assert "Hack" in text
    assert "$1,000,000" in text


async def _no_redis() -> None:
    return None


# ═══════════════════════════════════════════════════════════════════════
# HMAC
# ═══════════════════════════════════════════════════════════════════════


def test_sign_and_verify_hmac() -> None:
    payload = b'{"event": "critical_alert"}'
    sig = ra.sign_payload("secret123", payload)
    assert sig.startswith("sha256=")
    assert ra.verify_signature("secret123", sig, payload)
    assert not ra.verify_signature("wrong-secret", sig, payload)
    assert not ra.verify_signature("secret123", "sha256=deadbeef", payload)
    assert not ra.verify_signature("secret123", "", payload)


def test_sign_payload_string_and_bytes_equivalent() -> None:
    assert ra.sign_payload("s", "hello") == ra.sign_payload("s", b"hello")


def test_build_webhook_payload_shape() -> None:
    a = ra.CriticalAlert(title="Hack", chain="ethereum", source="searxng", amount_usd=5e6)
    p = ra.build_webhook_payload(a)
    assert p["event"] == "critical_alert"
    assert p["id"] == a.id
    assert p["title"] == "Hack"
    assert p["chain"] == "ethereum"
    assert p["amount_usd"] == 5e6


# ═══════════════════════════════════════════════════════════════════════
# Webhook registry + delivery
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_webhook_register_unregister_roundtrip() -> None:
    reg = ra.WebhookRegistry()
    rec = await reg.register("https://example.com/hook", name="test")
    assert rec["webhook_id"]
    assert rec["secret"]
    hooks = await reg.list()
    assert any(h["id"] == rec["webhook_id"] for h in hooks)
    assert await reg.unregister(rec["webhook_id"])
    hooks = await reg.list()
    assert not any(h["id"] == rec["webhook_id"] for h in hooks)


@pytest.mark.asyncio
async def test_webhook_register_generates_secret_when_absent() -> None:
    reg = ra.WebhookRegistry()
    rec = await reg.register("https://example.com/hook")
    assert len(rec["secret"]) >= 16


@pytest.mark.asyncio
async def test_deliver_webhooks_signed(monkeypatch: pytest.MonkeyPatch) -> Any:
    reg = ra.WebhookRegistry()
    rec = await reg.register("https://example.com/hook")
    alert = ra.CriticalAlert(title="Hack", source="test")

    received: dict[str, Any] = {}

    async def fake_post(
        url: Any,
        content: Any = None,
        headers: Any = None,
        **kwargs: Any,
    ) -> Any:
        received["url"] = url
        received["content"] = content
        received["headers"] = headers
        return httpx.Response(200)

    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda **kw: _FakeClient(fake_post))
    result = await ra.deliver_webhooks(alert, registry=reg)
    assert result["delivered"] == 1
    assert result["failed"] == 0
    body = received["content"].encode()
    sig = received["headers"]["X-Webhook-Signature"]
    assert ra.verify_signature(rec["secret"], sig, body)
    assert received["headers"]["X-Webhook-Id"] == rec["webhook_id"]
    assert received["headers"]["X-Webhook-Event"] == "critical_alert"


@pytest.mark.asyncio
async def test_deliver_webhooks_skips_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = ra.WebhookRegistry()
    await reg.register("https://example.com/hook")

    async def fail_post(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(ra.httpx, "AsyncClient", lambda **kw: _FakeClient(fail_post))
    result = await ra.deliver_webhooks(ra.CriticalAlert(title="Hack", source="test"), registry=reg)
    assert result["delivered"] == 0
    assert result["failed"] == 1


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in with a custom ``post``."""

    def __init__(self, post: Any, **kwargs: Any) -> None:
        self._post = post

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        return await self._post(*args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return await self._post(*args, **kwargs)

    async def aclose(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
# SearXNG polling
# ═══════════════════════════════════════════════════════════════════════

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>searx</title>
<item>
  <title>ALERT: $4.2M exploited from Solana lending protocol</title>
  <link>https://example.com/t/123</link>
  <pubDate>Tue, 04 Aug 2026 12:00:00 +0000</pubDate>
  <description>Oracle manipulation drained funds from the lending market.</description>
</item>
<item>
  <title>Daily market recap is quiet today</title>
  <link>https://example.com/t/456</link>
  <pubDate>Tue, 04 Aug 2026 11:00:00 +0000</pubDate>
  <description>Markets trading sideways, no incidents.</description>
</item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_poll_searxng_exploits_rss() -> None:
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(200, text=_RSS))
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await ra.poll_searxng_exploits(client, accounts=("CertiKAlert",))
    assert len(alerts) == 1
    assert "CertiKAlert" in alerts[0].title
    assert alerts[0].source == "searxng"
    assert alerts[0].chain == "solana"
    assert alerts[0].amount_usd == pytest.approx(4_200_000, rel=1e-3)


@pytest.mark.asyncio
async def test_poll_searxng_exploits_json_fallback() -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        # RSS request (first) returns non-RSS body → JSON fallback.
        if request.url.params.get("format") == "rss":
            return httpx.Response(200, text="<html>no feeds</html>")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "BlockSec: bridge exploit drained $9M",
                        "url": "https://example.com/x",
                        "content": "Attacker used a signature replay across chains.",
                    },
                    {
                        "title": "unrelated tweet",
                        "url": "https://example.com/y",
                        "content": "gm everyone",
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler=handler)
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await ra.poll_searxng_exploits(client, accounts=("BlockSecAlert",))
    assert len(alerts) == 1
    assert "BlockSecAlert" in alerts[0].title
    assert "9M" in alerts[0].title


@pytest.mark.asyncio
async def test_poll_searxng_exploits_fails_open() -> None:
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await ra.poll_searxng_exploits(client, accounts=("CertiKAlert",))
    assert alerts == []


# ═══════════════════════════════════════════════════════════════════════
# ClickHouse on-chain queries
# ═══════════════════════════════════════════════════════════════════════


def test_dex_whale_sql_replaces_db() -> None:
    sql = ra._dex_whale_sql("wallet_memory")
    assert "wallet_memory.dex_events" in sql
    assert "{threshold:Float64}" in sql
    assert "__DB__" not in sql


def test_new_address_sql_replaces_db() -> None:
    sql = ra._new_address_sql("rmi")
    assert "rmi.dex_events d" in sql
    assert "{new_max:UInt32}" in sql


def test_oracle_pattern_sql_replaces_db() -> None:
    sql = ra._oracle_pattern_sql("wallet_memory")
    assert "wallet_memory.dex_events" in sql
    assert "{whale_min:UInt32}" in sql


class _FakeCh:
    """clickhouse-connect query() stand-in returning canned rows."""

    def __init__(self, rows_by_contains: dict[str, list[tuple[Any, ...]]]) -> None:
        self._rows = rows_by_contains
        self.queries: list[str] = []

    def query(self, sql: Any, parameters: Any = None, settings: Any = None) -> Any:
        self.queries.append(sql)
        rows = []
        for needle, hits in self._rows.items():
            if needle in sql:
                rows = hits
                break
        return _FakeResult(rows)


class _FakeResult:
    def __init__(self, rows: Any) -> None:
        self.result_rows = rows


@pytest.mark.asyncio
async def test_poll_onchain_anomalies_candidates() -> None:
    ch = _FakeCh(
        {
            "HAVING": [("ethereum", "0xpair1", "0xt0", "0xt1", 5, 2_500_000, "2026-08-04")],
            "<= {new_max": [
                (
                    "base",
                    "2026-08-04",
                    "0xtxh",
                    "uniswap",
                    "0xpair2",
                    "A",
                    "B",
                    "0xs",
                    "0xnew",
                    1_500_000,
                )
            ],
            "ORDER BY volume_usd": [
                (
                    "arbitrum",
                    "2026-08-04",
                    "0xtxh2",
                    "curve",
                    "0xpair3",
                    "C",
                    "D",
                    "0xs2",
                    "0xr2",
                    3_000_000,
                )
            ],
        }
    )
    alerts = await ra.poll_onchain_anomalies(ch=ch, db="wallet_memory")
    assert len(alerts) >= 3
    sources = {a.source for a in alerts}
    assert "clickhouse-dex" in sources
    assert "clickhouse-new-address" in sources
    assert "clickhouse-oracle" in sources


@pytest.mark.asyncio
async def test_poll_onchain_anomalies_fails_open() -> None:
    alerts = await ra.poll_onchain_anomalies(ch=_BoomCh(), db="wallet_memory")
    assert alerts == []


class _BoomCh:
    def query(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("clickhouse exploded")


# ═══════════════════════════════════════════════════════════════════════
# RPC monitors
# ═══════════════════════════════════════════════════════════════════════

_RPC_BLOCK = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "number": "0x100",
        "transactions": [
            {"hash": "0xaaa", "from": "0xf1", "to": "0xf2", "value": "0x3635c9adc5dea00000"},
            {"hash": "0xbbb", "from": "0xf3", "to": "0xf4", "value": "0x1"},
        ],
    },
}


def _rpc_handler(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(ra, "ETH_RPC_URL", "https://example.invalid/rpc")
    monkeypatch.setattr(ra, "LARGE_TX_USD", 1_000_000.0)

    async def fake_eth_price(_symbol: str) -> float:
        assert _symbol == "ethereum"
        return 3000.0

    monkeypatch.setattr(ra, "resolve_native_price", fake_eth_price)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "eth_blockNumber":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x100"})
        return httpx.Response(200, json=_RPC_BLOCK)

    return handler


@pytest.mark.asyncio
async def test_poll_ethereum_rpc_filters_large(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _rpc_handler(monkeypatch)
    transport = httpx.MockTransport(handler=handler)
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await ra.poll_ethereum_rpc(client)
    assert len(alerts) == 1
    assert alerts[0].tx_hash == "0xaaa"
    # 10^21 wei = 1000 ETH at the mocked $3000 -> $3,000,000
    assert alerts[0].amount_usd == pytest.approx(3_000_000, rel=1e-3)


@pytest.mark.asyncio
async def test_poll_ethereum_rpc_disabled_when_no_url() -> None:
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await ra.poll_ethereum_rpc(client) == []


@pytest.mark.asyncio
async def test_poll_solana_rpc(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(ra, "SOLANA_RPC_URL", "https://example.invalid/sol")
    monkeypatch.setattr(ra, "LARGE_TX_USD", 1_000_000.0)

    async def fake_sol_price(_symbol: str) -> float:
        assert _symbol == "solana"
        return 150.0

    monkeypatch.setattr(ra, "resolve_native_price", fake_sol_price)
    # Threshold: 1e6 / 150 * 1e9 lamports ≈ 6.67e12 — delta below is 7e12.
    block = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "transactions": [
                {
                    "meta": {
                        "preBalances": [7_000_000_000_000, 100],
                        "postBalances": [0, 7_000_000_000_100],
                    },
                    "transaction": {"signatures": ["sig1"]},
                },
                {
                    "meta": {"preBalances": [100], "postBalances": [100]},
                    "transaction": {"signatures": ["sig2"]},
                },
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "getSlot":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 99})
        return httpx.Response(200, json=block)

    transport = httpx.MockTransport(handler=handler)
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await ra.poll_solana_rpc(client)
    assert len(alerts) == 1
    assert alerts[0].tx_hash == "sig1"
    assert alerts[0].chain == "solana"


@pytest.mark.asyncio
async def test_poll_solana_rpc_disabled_when_no_url() -> None:
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await ra.poll_solana_rpc(client) == []


# ═══════════════════════════════════════════════════════════════════════
# Store + dedup + API helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_store_record_dedup_and_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    store = ra.LiveThreatStore()
    alert = ra.CriticalAlert(title="Hack", source="test", detected_at=datetime.now(UTC).isoformat())
    assert await store.record(alert) is True
    assert await store.record(alert) is False  # dedup
    assert len(store.live_threats()) == 1
    assert store.live_threats()[0]["id"] == alert.id
    store.clear()
    assert store.live_threats() == []


@pytest.mark.asyncio
async def test_store_recent_alerts_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    store = ra.LiveThreatStore()
    old = ra.CriticalAlert(
        title="Old hack",
        source="test",
        detected_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    )
    new = ra.CriticalAlert(
        title="Fresh hack", source="test", detected_at=datetime.now(UTC).isoformat()
    )
    await store.record(old)
    await store.record(new)
    recent = store.recent_alerts(minutes=60)
    titles = [a["title"] for a in recent]
    assert "Fresh hack" in titles
    assert "Old hack" not in titles


def test_module_helpers_degrade_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    assert ra.get_recent_alerts(minutes=60) == []


# ═══════════════════════════════════════════════════════════════════════
# Monitor pipeline
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_monitor_pipeline_runs_every_step(monkeypatch: pytest.MonkeyPatch) -> Any:
    """All four pipeline stages execute (counters increment) for a new alert."""
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    calls = {"nats": 0, "ch": 0, "tg": 0, "qd": 0, "wh": 0}

    class FakeBus:
        def __init__(self, subject: Any = None, **kwargs: Any) -> None:
            self.subject = subject

        async def connect(self) -> str:
            return "redis"

        async def publish_payload(self, payload: Any) -> bool:
            calls["nats"] += 1
            return True

        async def close(self) -> None:
            pass

    def fake_ch_log(alert: Any) -> Any:
        calls["ch"] += 1
        return True

    async def fake_tg(alert: Any) -> Any:
        calls["tg"] += 1
        return True

    async def fake_qd(alert: Any) -> Any:
        calls["qd"] += 1
        return True

    async def fake_deliver(
        alert: Any,
        registry: Any = None,
        client: Any = None,
    ) -> Any:
        calls["wh"] += 1
        return {"delivered": 1, "failed": 0, "errors": []}

    monkeypatch.setattr(ra, "AlertBus", FakeBus)
    monkeypatch.setattr(ra._critical_logger, "log", fake_ch_log)
    monkeypatch.setattr(ra, "_send_telegram", fake_tg)
    monkeypatch.setattr(ra, "store_qdrant_live_threat", fake_qd)
    monkeypatch.setattr(ra, "deliver_webhooks", fake_deliver)

    monitor = ra.ExploitMonitor()
    await monitor._handle_candidate(ra.CriticalAlert(title="Hack", source="test"))
    assert calls == {"nats": 1, "ch": 1, "tg": 1, "qd": 1, "wh": 1}
    assert monitor.stats()["alerts_fired"] == 1
    await monitor.close()


@pytest.mark.asyncio
async def test_monitor_dedup_skips_second_alert(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    calls = {"nats": 0}

    class FakeBus:
        def __init__(self, subject: Any = None, **kwargs: Any) -> None:
            pass

        async def connect(self) -> str:
            return "redis"

        async def publish_payload(self, payload: Any) -> bool:
            calls["nats"] += 1
            return True

        async def close(self) -> None:
            pass

    async def fake_deliver(
        alert: Any,
        registry: Any = None,
        client: Any = None,
    ) -> Any:
        return {"delivered": 0, "failed": 0, "errors": []}

    async def fake_tg(alert: Any) -> Any:
        return True

    async def fake_qd(alert: Any) -> Any:
        return True

    monkeypatch.setattr(ra, "AlertBus", FakeBus)
    monkeypatch.setattr(ra, "deliver_webhooks", fake_deliver)
    monkeypatch.setattr(ra, "_send_telegram", fake_tg)
    monkeypatch.setattr(ra, "store_qdrant_live_threat", fake_qd)
    monkeypatch.setattr(ra._critical_logger, "log", lambda alert: True)

    monitor = ra.ExploitMonitor()
    alert = ra.CriticalAlert(title="Hack", source="test", detected_at=datetime.now(UTC).isoformat())
    await monitor._handle_candidate(alert)
    await monitor._handle_candidate(alert)  # deduped
    assert calls["nats"] == 1
    assert monitor.stats()["alerts_fired"] == 1
    assert monitor.stats()["alerts_deduped"] == 1
    await monitor.close()


@pytest.mark.asyncio
async def test_start_stop_monitor_idempotent() -> None:
    await ra.stop_realtime_monitor()  # clean slate
    assert await ra.start_realtime_monitor() is True
    assert ra.is_realtime_monitor_running()
    # Idempotent: starting again does not spawn a second task.
    assert await ra.start_realtime_monitor() is True
    await ra.stop_realtime_monitor()
    assert not ra.is_realtime_monitor_running()


# ═══════════════════════════════════════════════════════════════════════
# Live native-asset pricing (2026-08-25 — ends frozen-price alerts)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resolve_price_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call inside the TTL window must NOT re-fetch."""
    ra._price_cache.clear()
    calls = {"n": 0}

    async def fake_fetch(symbol: str) -> float:
        calls["n"] += 1
        return 123.0

    monkeypatch.setattr(ra, "_fetch_live_price", fake_fetch)
    first = await ra.resolve_native_price("solana")
    second = await ra.resolve_native_price("solana")
    assert first == second == 123.0
    assert calls["n"] == 1
    ra._price_cache.clear()


@pytest.mark.asyncio
async def test_resolve_price_falls_back_to_env_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live fetch failure -> env constant; both unavailable -> 0 (stand down)."""
    ra._price_cache.clear()

    async def boom(_symbol: str) -> float:
        raise RuntimeError("coingecko down")

    monkeypatch.setattr(ra, "_fetch_live_price", boom)
    monkeypatch.setattr(ra, "SOL_PRICE_USD", 210.0)
    assert await ra.resolve_native_price("solana") == 210.0

    ra._price_cache.clear()  # drop the 210 entry before testing the 0 path
    monkeypatch.setattr(ra, "SOL_PRICE_USD", 0.0)
    assert await ra.resolve_native_price("solana") == 0.0
    ra._price_cache.clear()
