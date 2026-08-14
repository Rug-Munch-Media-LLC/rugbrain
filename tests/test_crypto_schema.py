"""Unit tests for rugbrain.crypto_schema — canonical crypto metadata helpers."""

import pytest

from rugbrain.crypto_schema import (
    detect_attack_vector,
    detect_chain,
    enrich_payload,
    infer_severity_from_amount,
    normalize_severity,
    normalize_source_type,
    parse_amount_usd,
    parse_date,
)


class TestDetectAttackVector:
    def test_reentrancy(self):
        assert detect_attack_vector("reentrancy guard bypassed in withdraw") == "reentrancy"

    def test_flash_loan(self):
        assert detect_attack_vector("used a flash loan to manipulate the pool") == "flash-loan"

    def test_oracle(self):
        assert (
            detect_attack_vector("price oracle manipulated to drain funds") == "oracle-manipulation"
        )

    def test_unknown_fallback(self):
        assert detect_attack_vector("some benign text") == "unknown"
        assert detect_attack_vector("") == "unknown"
        assert detect_attack_vector("nope", fallback="n/a") == "n/a"


class TestNormalizeSeverity:
    def test_synonyms(self):
        assert normalize_severity("CRITICAL") == "critical"
        assert normalize_severity("severe") == "critical"
        assert normalize_severity("moderate") == "medium"
        assert normalize_severity("informational") == "info"

    def test_empty_and_none(self):
        assert normalize_severity(None) == "unknown"
        assert normalize_severity("") == "unknown"

    def test_infer_from_amount(self):
        assert infer_severity_from_amount(50_000_000) == "critical"
        assert infer_severity_from_amount(5_000_000) == "high"
        assert infer_severity_from_amount(500_000) == "medium"
        assert infer_severity_from_amount(5_000) == "low"
        assert infer_severity_from_amount(0) == "unknown"


class TestDetectChain:
    def test_named_chain(self):
        assert detect_chain("exploit on bsc") == "bsc"
        assert detect_chain("solana SPL token scam") == "solana"

    def test_multi(self):
        assert detect_chain("bridged from ethereum to solana") == "multi"

    def test_evm_address(self):
        assert detect_chain("wallet 0xf92d55d70d60d3b331a781d9861b767e6bfb82ad") == "ethereum"

    def test_unknown(self):
        assert detect_chain("no chain mentioned") == "unknown"


class TestParseAmount:
    def test_numeric(self):
        assert parse_amount_usd(197_000_000) == 197_000_000

    def test_text_units(self):
        assert parse_amount_usd(None, "lost $197M in exploit") == 197_000_000
        assert parse_amount_usd(None, "stolen $1.2B total") == 1_200_000_000
        assert parse_amount_usd(None, "drained $50K") == 50_000

    def test_comma_separated(self):
        assert parse_amount_usd(None, "lost $12,500,000") == 12_500_000

    def test_none(self):
        assert parse_amount_usd(None, "no amount here") == 0.0


class TestParseDate:
    def test_iso(self):
        assert parse_date("2023-03-13") == "2023-03-13"

    def test_md_yyyy(self):
        assert parse_date("3/13/2023") == "2023-03-13"

    def test_word_month(self):
        assert parse_date("", "exploit on 28 Jan 2022") == "2022-01-28"

    def test_year_only(self):
        assert parse_date("2023") == "2023-01-01"

    def test_empty(self):
        assert parse_date(None, "") == ""


class TestNormalizeSourceType:
    def test_mappings(self):
        assert normalize_source_type("rekt_news") == "rekt_news"
        assert normalize_source_type("defihacklabs") == "defihacklabs"
        assert normalize_source_type("swc_registry") == "swc_registry"
        assert normalize_source_type("scamsniffer") == "scamsniffer"
        assert normalize_source_type("redis:scanner:wallet_labels:x") == "wallet_label"
        assert normalize_source_type("news:The Defiant") == "news"
        assert normalize_source_type("seed") == "seed"

    def test_empty(self):
        assert normalize_source_type(None) == "unknown"


class TestEnrichPayload:
    def test_fills_all_canonical_fields(self):
        payload = {
            "content": (
                "Euler Finance 2023: flash loan + reentrancy exploit drained "
                "$197M from the lending protocol on 2023-03-13"
            ),
            "metadata": {"category": "historical"},
            "source": "defihacklabs",
            "severity": "high",
        }
        enriched = enrich_payload(payload)
        assert enriched["attack_vector"] == "reentrancy"
        # severity escalates from the hardcoded "high" to amount-implied "critical"
        assert enriched["severity"] == "critical"
        assert enriched["amount_lost_usd"] == 197_000_000
        assert enriched["date"] == "2023-03-13"
        assert enriched["source_type"] == "defihacklabs"
        assert "date_ts" in enriched
        assert enriched["date_ts"] > 0
        # mirrored into metadata
        assert enriched["metadata"]["attack_vector"] == "reentrancy"
        assert enriched["metadata"]["amount_lost_usd"] == 197_000_000

    def test_keeps_existing_values(self):
        payload = {
            "content": "unrelated text",
            "metadata": {"attack_vector": "oracle-manipulation", "protocol": "Benqi"},
            "source": "seed",
            "chain": "avalanche",
        }
        enriched = enrich_payload(payload)
        assert enriched["attack_vector"] == "oracle-manipulation"
        assert enriched["protocol"] == "Benqi"
        assert enriched["chain"] == "avalanche"

    def test_severity_inferred_from_amount(self):
        payload = {
            "content": "drained $50,000,000 from protocol",
            "metadata": {},
            "source": "rekt_news",
        }
        enriched = enrich_payload(payload)
        assert enriched["severity"] == "critical"

    def test_original_payload_untouched(self):
        payload = {"content": "some content", "metadata": {}, "source": "seed"}
        enrich_payload(payload)
        assert "attack_vector" not in payload
        assert "severity" not in payload


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
