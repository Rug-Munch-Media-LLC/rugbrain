"""Tests for the 2026-09-28 corpora ingest family.

Covers the five structured sources added to close the ``rug_events`` /
``scam_signatures`` gap:

  * ``fraudcoins_signatures``     — holder concentration + GoPlus privileges
  * ``scythe_radar_datasets``     — documented dead tokens + OFAC addresses
  * ``forta_malicious_contracts`` — labelled malicious/benign contracts
  * ``crpwarner_rug_events``      — ground-truth rug events with loss figures
  * ``vulnerability_severity``    — severity-graded findings + Solana audit Q&A

Every test is offline: ``respx`` mocks HTTP and the normalizers are pure
functions over fixture-shaped dicts, so no network or Qdrant is touched.
"""

from __future__ import annotations

import importlib as _il

import pytest
import respx

ingest_sources = _il.import_module("rugbrain.ingest_sources")
mod = ingest_sources

from rugbrain.ingest_sources import (
    crpwarner_rug_events,
    forta_malicious_contracts,
    fraudcoins_signatures,
    scythe_radar_datasets,
    vulnerability_severity,
)

# ── registration ────────────────────────────────────────────────────────

REQUIRED_NEW = {
    "fraudcoins_signatures": "scam_signature",
    "scythe_radar_datasets": "rug_event",
    "forta_malicious_contracts": "scam_signature",
    "crpwarner_rug_events": "rug_event",
    "vulnerability_severity": "audit_finding",
}


def test_new_sources_registered() -> None:
    names = set(mod.list_sources())
    assert set(REQUIRED_NEW) <= names, f"missing: {set(REQUIRED_NEW) - names}"
    for name, doc_type in REQUIRED_NEW.items():
        assert mod.get_source(name).doc_type == doc_type


# ── fraudcoins ──────────────────────────────────────────────────────────

_FC_ENTRY = {
    "coinId": "rain",
    "chain": "arbitrum",
    "address": "0x25118290e6a5f4139381d072181157035864099d",
    "measuredAt": "2026-09-27T05:35:33.059Z",
    "isMintable": None,
    "ownerChangeBalance": None,
    "transferPausable": None,
    "isBlacklisted": None,
    "canTakeBackOwnership": None,
    "hiddenOwner": True,
    "selfDestruct": None,
    "isProxy": False,
    "isOpenSource": True,
}
_FC_CONC = {"coins": {"rain": {"top10Pct": 85.7, "top10PctRaw": 96.2, "flagged": False}}}
_FC_HIST = {"coins": {"rain": [["2026-08-31", 85.7], ["2026-09-01", 85.7]]}}


def test_fraudcoins_token_to_doc() -> None:
    doc = fraudcoins_signatures.token_to_doc(
        "arbitrum:0x25118290e6a5f4139381d072181157035864099d",
        _FC_ENTRY,
        _FC_CONC,
        _FC_HIST,
    )
    assert doc is not None
    assert doc.doc_type == "scam_signature"
    assert doc.metadata["collection"] == "scam_signatures"
    assert doc.metadata["token_address"] == _FC_ENTRY["address"]
    assert doc.metadata["chain"] == "arbitrum"
    assert doc.metadata["top10_pct"] == 85.7
    assert doc.metadata["top10_pct_raw"] == 96.2
    assert doc.metadata["hiddenOwner"] is True
    assert doc.metadata["isProxy"] is False
    assert doc.metadata["live_privileges"] == ["hiddenOwner"]
    assert "owner-privilege" in doc.metadata["labels"]
    assert doc.metadata["measured_to"] == "2026-09-01"
    assert doc.doc_id == f"fraudcoins:arbitrum:{_FC_ENTRY['address']}"


def test_fraudcoins_null_privilege_stays_none() -> None:
    """Upstream null means 'not reported' — must NOT be coerced to False."""
    doc = fraudcoins_signatures.token_to_doc("arbitrum:0xabc", _FC_ENTRY, {}, {})
    assert doc is not None
    assert doc.metadata["isMintable"] is None
    assert doc.metadata["isBlacklisted"] is None
    assert "top10_pct" not in doc.metadata


def test_fraudcoins_derives_address_from_key() -> None:
    entry = dict(_FC_ENTRY)
    entry.pop("address")
    doc = fraudcoins_signatures.token_to_doc("base:0xderived", entry, {}, {})
    assert doc is not None
    assert doc.metadata["token_address"] == "0xderived"
    assert doc.metadata["chain"] == "base"


def test_fraudcoins_rejects_empty_entry() -> None:
    assert fraudcoins_signatures.token_to_doc("x", {}, {}, {}) is None
    assert fraudcoins_signatures.token_to_doc("x", "not-a-dict", {}, {}) is None


@respx.mock
async def test_fraudcoins_fetch_mocked() -> None:
    src = mod.get_source("fraudcoins_signatures")
    respx.get(fraudcoins_signatures.PERMISSIONS_URL).respond(
        200, json={"tokenCount": 1, "tokens": {"arbitrum:0xabc": _FC_ENTRY}}
    )
    respx.get(fraudcoins_signatures.CONCENTRATION_URL).respond(200, json=_FC_CONC)
    respx.get(fraudcoins_signatures.HISTORY_URL).respond(200, json=_FC_HIST)
    docs = await src.fetch()
    assert len(docs) == 1
    assert docs[0].metadata["top10_pct"] == 85.7
    # idempotent across re-fetch
    docs2 = await src.fetch()
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


@respx.mock
async def test_fraudcoins_empty_permissions_fails_open() -> None:
    src = mod.get_source("fraudcoins_signatures")
    respx.get(fraudcoins_signatures.PERMISSIONS_URL).respond(200, json={"tokens": {}})
    respx.get(fraudcoins_signatures.CONCENTRATION_URL).respond(200, json={})
    respx.get(fraudcoins_signatures.HISTORY_URL).respond(200, json={})
    assert await src.fetch() == []


# ── scythe radar ────────────────────────────────────────────────────────

_DEAD_ROW = {
    "contract_address": "0xcae117ca6bc8a341d2e7207f30e180f0e5618b9d",
    "token_name": "ARK / 方舟之境",
    "token_name_en": "ARK · Ark Realm",
    "chain": "BSC",
    "status": "重启盘",
    "status_en": "Rugged once · Relaunch",
    "outcome": "砸盘已发生 · 正在重启",
    "mechanism_en": "Treasury/reserve narrative + unlimited mint dumps",
    "early_warning": "GoPlus 检出 1 项风险标记：无限增发砸盘",
    "victims_note": "单次砸盘套现 $2626 万",
    "timeline_span": "砸盘 → 重启",
    "detail_url": "https://scytheradar.com/death/ark-ark-realm.html",
}
_OFAC_ROW = {
    "address": "13ybqr2cp1yy3xqq2qngapb7ca1o4ugeq6",
    "chain": "btc",
    "asset": "XBT",
    "sanctions_program": "CYBER2",
    "entity_name": "ANDREYEV, Anton Nikolaeyvich",
    "sdn_entry": "29703",
}


def test_scythe_dead_token_to_doc() -> None:
    doc = scythe_radar_datasets.dead_token_to_doc(_DEAD_ROW)
    assert doc is not None
    assert doc.doc_type == "rug_event"
    assert doc.metadata["collection"] == "rug_events"
    assert doc.metadata["token_address"] == _DEAD_ROW["contract_address"]
    assert doc.metadata["chain"] == "BSC"
    assert doc.metadata["rug_outcome"] == "rugged_relaunch"
    assert "unlimited mint" in doc.metadata["rug_mechanism"]
    assert doc.metadata["token_name"] == "ARK · Ark Realm"
    assert doc.doc_id == f"scythe_radar:dead_token:BSC:{_DEAD_ROW['contract_address']}"
    assert "2626" in doc.metadata["victims_note"]


def test_scythestatus_map_covers_english() -> None:
    row = dict(_DEAD_ROW, status="lp_drained")
    doc = scythe_radar_datasets.dead_token_to_doc(row)
    assert doc is not None
    assert doc.metadata["rug_outcome"] == "lp_drained"


def test_scythe_dead_token_requires_address() -> None:
    assert scythe_radar_datasets.dead_token_to_doc({"chain": "BSC"}) is None
    assert scythe_radar_datasets.dead_token_to_doc({}) is None


def test_scythe_ofac_to_doc() -> None:
    doc = scythe_radar_datasets.ofac_to_doc(_OFAC_ROW)
    assert doc is not None
    assert doc.doc_type == "sanction_entity"
    assert doc.metadata["collection"] == "known_scams"
    assert doc.metadata["entity_type"] == "wallet"
    assert doc.metadata["sanctions_program"] == "CYBER2"
    assert doc.metadata["entity_name"] == "ANDREYEV, Anton Nikolaeyvich"
    assert "ofac" in doc.metadata["labels"]


def test_scythe_ofac_requires_address() -> None:
    assert scythe_radar_datasets.ofac_to_doc({"chain": "btc"}) is None


@respx.mock
async def test_scythe_fetch_mocked() -> None:
    import csv as _csv
    import io as _io

    def to_csv(rows):
        buf = _io.StringIO()
        w = _csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return buf.getvalue()

    src = mod.get_source("scythe_radar_datasets")
    respx.get(scythe_radar_datasets.DEAD_TOKENS_URL).respond(200, text=to_csv([_DEAD_ROW]))
    respx.get(scythe_radar_datasets.OFAC_URL).respond(200, text=to_csv([_OFAC_ROW]))
    docs = await src.fetch()
    assert len(docs) == 2
    assert {d.metadata["collection"] for d in docs} == {"rug_events", "known_scams"}


@respx.mock
async def test_scythe_fetch_all_404_fails_open() -> None:
    src = mod.get_source("scythe_radar_datasets")
    respx.get(scythe_radar_datasets.DEAD_TOKENS_URL).respond(404)
    respx.get(scythe_radar_datasets.OFAC_URL).respond(500)
    assert await src.fetch() == []


# ── forta ───────────────────────────────────────────────────────────────

_FORTA_ROWS = [
    {
        "contract_address": "0xbad",
        "contract_name": "EvilToken",
        "contract_etherscan_label": "Honeypot",
        "contract_tag": "proxy",
        "contract_creator": "0xdeployer",
        "contract_creation_tx": "0xdead",
        "malicious": True,
    },
    {
        "contract_address": "0xgood",
        "contract_name": "SafeToken",
        "malicious": False,
    },
    {"contract_name": "NoAddress", "malicious": True},
]


def test_forta_rows_to_docs() -> None:
    docs = forta_malicious_contracts.rows_to_docs(_FORTA_ROWS, cap=10)
    assert len(docs) == 2  # the address-less row is dropped
    bad, good = docs
    assert bad.metadata["malicious"] is True
    assert bad.metadata["severity"] == "critical"
    assert bad.metadata["contract_creator"] == "0xdeployer"
    assert bad.metadata["chain"] == "ethereum"
    assert bad.metadata["entity_type"] == "contract"
    assert "malicious-contract" in bad.metadata["labels"]
    assert good.metadata["malicious"] is False
    assert good.metadata["severity"] == "low"
    assert "benign-contract" in good.metadata["labels"]
    assert bad.doc_id == "forta_malicious_contracts:0xbad"


def test_forta_respects_cap() -> None:
    assert len(forta_malicious_contracts.rows_to_docs(_FORTA_ROWS, cap=1)) == 1


@respx.mock
async def test_forta_fetch_non200_fails_open() -> None:
    src = mod.get_source("forta_malicious_contracts")
    respx.get(forta_malicious_contracts.PARQUET_URL).respond(403)
    assert await src.fetch(limit=5) == []


# ── crpwarner ───────────────────────────────────────────────────────────

_CRP_ROW = {
    "Project Name": "Some DeFi",
    "Classification": "Contract-related Rug Pull",
    "Pattern": "Hidden Mint Function",
    "Loss": 1_200,
    "Source": "rekt.news",
    "Open Source": "Yes",
    "Enough Information": "Yes",
}


def test_crpwarner_event_to_doc() -> None:
    doc = crpwarner_rug_events.event_to_doc(_CRP_ROW)
    assert doc is not None
    assert doc.doc_type == "rug_event"
    assert doc.metadata["collection"] == "rug_events"
    assert doc.metadata["project"] == "Some DeFi"
    assert doc.metadata["rug_classification"] == "Contract-related Rug Pull"
    assert doc.metadata["rug_pattern"] == "Hidden Mint Function"
    assert doc.metadata["loss_kusd"] == 1200.0
    assert "manual-groundtruth" in doc.metadata["labels"]
    assert doc.doc_id == "crpwarner:rug_event:some-defi"


def test_crpwarner_loss_parsing() -> None:
    assert crpwarner_rug_events._loss_to_usd_k("1,200") == 1200.0
    assert crpwarner_rug_events._loss_to_usd_k(None) is None
    assert crpwarner_rug_events._loss_to_usd_k("n/a") is None
    doc = crpwarner_rug_events.event_to_doc({"Project Name": "X", "Loss": "n/a"})
    assert doc is not None
    assert "loss_kusd" not in doc.metadata


def test_crpwarner_requires_project() -> None:
    assert crpwarner_rug_events.event_to_doc({}) is None
    assert crpwarner_rug_events.event_to_doc({"Classification": "x"}) is None


@respx.mock
async def test_crpwarner_gated_on_unlicensed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No repo licence: must refuse to store unless explicitly opted in."""
    src = mod.get_source("crpwarner_rug_events")
    monkeypatch.delenv(crpwarner_rug_events.ALLOW_ENV, raising=False)
    assert await src.fetch() == []


@respx.mock
async def test_crpwarner_opt_in_then_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import io as _io

    # pandas is an OPTIONAL dependency (used only for the .xlsx rug-events
    # reader). Skip rather than fail the suite when it is not installed.
    pd = pytest.importorskip("pandas")

    src = mod.get_source("crpwarner_rug_events")
    monkeypatch.setenv(crpwarner_rug_events.ALLOW_ENV, "1")
    buf = _io.BytesIO()
    pd.DataFrame([_CRP_ROW]).to_excel(buf, index=False)
    respx.get(crpwarner_rug_events.RAW_URL).respond(200, content=buf.getvalue())
    docs = await src.fetch()
    assert len(docs) == 1
    assert docs[0].metadata["loss_kusd"] == 1200.0


# ── vulnerability severity ──────────────────────────────────────────────


def test_severity_row_to_doc() -> None:
    doc = vulnerability_severity.severity_row_to_doc(
        {"function": "function f() { ... }", "severity": "Critical"}, "train"
    )
    assert doc is not None
    assert doc.doc_type == "audit_finding"
    assert doc.metadata["collection"] == "contract_audits"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["chain"] == "evm"
    assert "train" in doc.metadata["labels"]
    assert doc.content.startswith("[critical]")


def test_severity_requires_function() -> None:
    assert vulnerability_severity.severity_row_to_doc({"severity": "high"}, "train") is None


def test_solana_item_to_doc() -> None:
    doc = vulnerability_severity.solana_item_to_doc(
        {"question": "How to secure a PDA?", "answer": "Validate seeds", "topic": "pda"}, 3
    )
    assert doc is not None
    assert doc.metadata["chain"] == "solana"
    assert doc.metadata["topic"] == "pda"
    assert doc.doc_id == "solana_vanguard:3"
    assert "Q: How to secure a PDA?" in doc.content


def test_solana_accepts_bare_string() -> None:
    doc = vulnerability_severity.solana_item_to_doc("anchor security notes", 1)
    assert doc is not None
    assert "anchor security notes" in doc.content


def test_solana_rejects_garbage() -> None:
    assert vulnerability_severity.solana_item_to_doc({"question": "", "answer": ""}, 0) is None
    assert vulnerability_severity.solana_item_to_doc(42, 0) is None


@respx.mock
async def test_vulnerability_severity_fetch_parses_live_shape() -> None:
    import io as _io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"function": ["f1", "f2"], "severity": ["High", "Low"]})
    buf = _io.BytesIO()
    pq.write_table(table, buf)
    src = mod.get_source("vulnerability_severity")
    respx.get(vulnerability_severity.SEVERITY_URL).respond(200, content=buf.getvalue())
    respx.get(vulnerability_severity.SEVERITY_URL_TEST).respond(200, content=buf.getvalue())
    respx.get(vulnerability_severity.SOLANA_URL).respond(
        200, json=[{"question": "q1", "answer": "a1"}]
    )
    docs = await src.fetch()
    assert len(docs) == 5  # 2 train + 2 test + 1 solana
    assert any(d.metadata["chain"] == "solana" for d in docs)
    assert any(d.metadata["severity"] == "high" for d in docs)


@respx.mock
async def test_vulnerability_severity_all_fail_open() -> None:
    src = mod.get_source("vulnerability_severity")
    respx.get(vulnerability_severity.SEVERITY_URL).respond(404)
    respx.get(vulnerability_severity.SEVERITY_URL_TEST).respond(404)
    respx.get(vulnerability_severity.SOLANA_URL).respond(500)
    assert await src.fetch() == []
