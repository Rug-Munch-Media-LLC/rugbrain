"""
Tests for rmi_rag.alerts — real-time crypto security alert ingestion.

Covers alert identity determinism, vector/chain detection, RSS/API parsing,
pollers (httpx MockTransport), rate-limit/dedup state, Qdrant storage (respx),
bus publishing fallback, and ClickHouse logging degradation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from rmi_rag import alerts as mod

# ── Model / helpers ──────────────────────────────────────────────────────


def test_alert_id_deterministic():
    a = mod.SecurityAlert(source="rekt", title="Curve Hack", date="2023-07-30")
    b = mod.SecurityAlert(source="rekt", title="Curve Hack", date="2023-07-30")
    assert a.id == b.id
    c = mod.SecurityAlert(source="rekt", title="Curve Hack", date="2024-01-01")
    assert a.id != c.id


def test_alert_text_contains_fields():
    a = mod.SecurityAlert(
        source="defillama",
        title="Super Sushi Samurai",
        protocol="Super Sushi Samurai",
        amount_lost_usd=4_800_000,
        description="Infinite mint and dump.",
        chain="blast",
    )
    text = a.text()
    assert "Super Sushi Samurai" in text
    assert "$4,800,000" in text
    assert "Infinite mint and dump." in text


def test_detect_vector_keywords():
    assert mod.detect_vector("reentrancy vulnerability in withdraw") == "reentrancy"
    assert mod.detect_vector("oracle price manipulation attack") == "oracle-manipulation"
    assert mod.detect_vector("liquidity removed exit scam") == "rug-pull"
    assert mod.detect_vector("nothing suspicious here") == "unknown"


def test_detect_chain_keywords():
    assert mod.detect_chain("exploit on solana") == "solana"
    assert mod.detect_chain("hack on ethereum mainnet") == "ethereum"
    assert mod.detect_chain("cross-chain bridge hack") == "multi"
    assert mod.detect_chain("0xabc123456789abcdef") == "ethereum"


def test_parse_date_iso_rfc822():
    assert mod._parse_date_iso("Tue, 04 Aug 2026 12:00:00 +0000") == "2026-08-04"
    assert mod._parse_date_iso("2026-08-04T10:00:00Z") == "2026-08-04"
    assert mod._parse_date_iso("") == ""


# ── Pollers (mocked HTTP) ────────────────────────────────────────────────


async def _poll_json(poller, payload):
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        return await poller(client, datetime.now(UTC).timestamp())


async def _poll_text(poller, text):
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(200, text=text))
    async with httpx.AsyncClient(transport=transport) as client:
        return await poller(client, datetime.now(UTC).timestamp())


async def test_poll_defillama_recent():
    now = datetime.now(UTC).timestamp()
    payload = [
        {
            "date": int(now) - 3600,  # 1h ago
            "name": "Super Sushi Samurai",
            "amount": 4800000,
            "chain": ["Blast"],
            "technique": "Infinite Mint and Dump",
            "classification": "Protocol Logic",
            "targetType": "Gaming",
            "language": "Solidity",
        },
        {
            "date": int(now) - 60 * 60 * 24 * 30,  # 30d ago → outside window
            "name": "Old Hack",
            "amount": 1000,
            "chain": ["Ethereum"],
            "technique": "",
            "classification": "",
        },
    ]
    alerts = await _poll_json(mod.poll_defillama, payload)
    assert len(alerts) == 1
    assert alerts[0].protocol == "Super Sushi Samurai"
    assert alerts[0].amount_lost_usd == 4800000
    assert alerts[0].severity == "high"  # $4.8M < $10M critical threshold


async def test_poll_defillama_http_error():
    transport = httpx.MockTransport(handler=lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        alerts = await mod.poll_defillama(client, datetime.now(UTC).timestamp())
    assert alerts == []


async def test_poll_coingecko():
    payload = [
        {
            "id": "shibainu-new",
            "symbol": "SHIBX",
            "name": "Shiba New",
            "current_price": 0.0001,
            "market_cap": 100000,
        }
    ]
    alerts = await _poll_json(mod.poll_coingecko, payload)
    assert len(alerts) == 1
    assert "Shiba New" in alerts[0].title
    assert alerts[0].attack_vector == "rug-pull"


async def test_poll_rekt_leaderboard_next_data():
    now = datetime.now(UTC).timestamp()
    iso = datetime.fromtimestamp(now, tz=UTC).date().isoformat()
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        f'{{"props":{{"pageProps":{{"leaderboard":[{{'
        f'"slug":"curve-rekt","title":"Curve","excerpt":"CRV hack",'
        f'"date":"{iso}","tags":["ethereum","reentrancy"],'
        f'"rekt":{{"amount":62000000,"date":"{iso}"}}}}]}}}}}}'
        "</script>"
    )
    alerts = await _poll_text(mod.poll_rekt, html)
    assert len(alerts) == 1
    assert alerts[0].protocol == "Curve"
    assert alerts[0].amount_lost_usd == 62000000


def test_parse_rss_items():
    xml = (
        "<rss><channel><item><title>New hack alert</title>"
        "<link>https://example.com/1</link>"
        "<pubDate>Tue, 04 Aug 2026 10:00:00 +0000</pubDate>"
        "<description>Exploit detected</description></item></channel></rss>"
    )
    items = mod._parse_rss_items(xml, "certikalert")
    assert len(items) == 1
    assert items[0][0] == "New hack alert"
    assert items[0][2] == "2026-08-04"


# ── State (rate limit + dedup, in-memory fallback) ───────────────────────


def _rate_limited_for_test(state, source, now, interval) -> bool:
    last = state._memory.get(f"poll:{source}")
    if last is None:
        return False
    return (now - float(last)) < interval


async def test_alert_state_memory_fallback():
    state = mod.AlertState()
    await state.set_last_poll("defillama", 100.0)
    assert await state.last_poll("defillama") == 100.0
    assert _rate_limited_for_test(state, "defillama", now=100.0 + 299, interval=300)
    assert not _rate_limited_for_test(state, "defillama", now=100.0 + 301, interval=300)

    aid = mod.SecurityAlert(source="x", title="y").id
    assert not await state.is_seen(aid)
    await state.mark_seen(aid)
    assert await state.is_seen(aid)


# ── Qdrant storage (respx) ───────────────────────────────────────────────


@respx.mock
async def test_store_alert_upsert():
    alert = mod.SecurityAlert(source="rekt", title="Curve Hack", date="2023-07-30")
    embedding = [0.1] * mod.EMBEDDING_DIM

    respx.get(f"{mod.QDRANT_URL}/collections/{mod.ALERT_COLLECTION}").respond(200, json={})
    respx.put(f"{mod.QDRANT_URL}/collections/{mod.ALERT_COLLECTION}/points").respond(200, json={})

    ok = await mod.store_alert(alert, embedding)
    assert ok is True


@respx.mock
async def test_store_alert_creates_collection():
    alert = mod.SecurityAlert(source="rekt", title="Curve Hack", date="2023-07-30")
    embedding = [0.1] * mod.EMBEDDING_DIM

    respx.get(f"{mod.QDRANT_URL}/collections/{mod.ALERT_COLLECTION}").respond(404, json={})
    respx.put(f"{mod.QDRANT_URL}/collections/{mod.ALERT_COLLECTION}").respond(200, json={})
    respx.put(f"{mod.QDRANT_URL}/collections/{mod.ALERT_COLLECTION}/points").respond(200, json={})

    ok = await mod.store_alert(alert, embedding)
    assert ok is True


# ── Bus publishing ───────────────────────────────────────────────────────


async def test_alert_bus_publish_no_transport(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    bus = mod.AlertBus()
    await bus.connect()
    alert = mod.SecurityAlert(source="x", title="y")
    ok = await bus.publish(alert)
    await bus.close()
    assert ok is False  # no NATS, no Redis → drop, never crash


# ── ClickHouse logger degradation ────────────────────────────────────────


def test_clickhouse_logger_unavailable(monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    logger = mod.ClickHouseAlertLogger()
    assert logger._client is None
    alert = mod.SecurityAlert(source="x", title="y")
    # clickhouse_connect is not installed in the dev venv → _get() returns None
    ok = logger.log(alert)
    assert ok is False


# ── poll_once orchestration with a stubbed source ────────────────────────


async def test_poll_once_fake_source(monkeypatch):
    async def fake_poller(client, now):
        return [mod.SecurityAlert(source="fake", title="Fake Incident", date="2026-08-01")]

    monkeypatch.setattr(mod, "SOURCE_POLLERS", {"fake": fake_poller})
    # Force in-memory state (no Redis), no bus.

    async def _no_client(self):
        return False

    monkeypatch.setattr(mod.AlertState, "_client", _no_client)
    monkeypatch.setenv("NATS_URL", "")

    results = await mod.poll_once(dry_run=True)
    assert results["sources"]["fake"]["detected"] == 1
    assert results["sources"]["fake"]["new"] == 1
    assert results["total_new"] == 1


async def test_poll_once_dedups_within_pass(monkeypatch):
    async def fake_poller(client, now):
        alert = mod.SecurityAlert(source="fake", title="Fake Incident", date="2026-08-01")
        return [alert, alert]  # same incident detected twice in one pass

    monkeypatch.setattr(mod, "SOURCE_POLLERS", {"fake": fake_poller})

    async def _no_client(self):
        return False

    monkeypatch.setattr(mod.AlertState, "_client", _no_client)
    monkeypatch.setenv("NATS_URL", "")

    results = await mod.poll_once(dry_run=True)
    assert results["sources"]["fake"]["detected"] == 2
    assert results["sources"]["fake"]["new"] == 1
    assert results["sources"]["fake"]["skipped_dup"] == 1
