"""Address extraction is a lead generator; these tests pin the false positives.

A transaction hash must never become a token, zero/burn sentinels carry no
intelligence, and ordinary prose must not become a Solana mint.
"""

from __future__ import annotations

from rugbrain.scam_watch.addresses import extract_addresses

EVM = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_extracts_evm_address_and_normalises_case():
    hits = extract_addresses(f"CA: {EVM.upper().replace('0X', '0x')} join now", field="title")
    assert len(hits) == 1
    assert hits[0].chain == "evm"
    assert hits[0].address == EVM.lower()
    assert hits[0].field == "title"


def test_transaction_hash_is_not_a_contract():
    tx = "0x" + "ab" * 32
    assert extract_addresses(f"proof {tx}") == ()


def test_zero_and_burn_addresses_are_ignored():
    assert extract_addresses("0x" + "00" * 20) == ()
    assert extract_addresses("0x" + "00" * 18 + "dead") == ()


def test_extracts_solana_mint():
    hits = extract_addresses(f"mint {SOL} live")
    assert [hit.chain for hit in hits] == ["solana"]
    assert hits[0].address == SOL


def test_long_english_word_is_not_a_solana_mint():
    prose = "thisisanordinarylowercasewordthatisnotamint"
    assert extract_addresses(f"the {prose} thing") == ()


def test_substring_of_longer_base58_run_is_not_matched():
    padded = "1" * 20 + SOL + "2" * 20
    assert all(hit.address != SOL for hit in extract_addresses(padded))


def test_duplicates_collapse_and_order_is_stable():
    hits = extract_addresses(f"{EVM} then {EVM.lower()} then {SOL}")
    assert [hit.address for hit in hits] == [EVM.lower(), SOL]
