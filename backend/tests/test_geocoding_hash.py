"""Unit tests for app.services.geocoding.hash — Wave 2-B revise (M1)."""

from __future__ import annotations

import hashlib

from app.services.geocoding.hash import address_hash, normalize_address


def test_normalize_collapses_whitespace() -> None:
    assert normalize_address("  東京都   千代田区  ") == "東京都 千代田区"


def test_normalize_nfkc_full_width_digits() -> None:
    # Full-width digits / spaces collapse to ASCII via NFKC.
    assert normalize_address("１２３　番地") == "123 番地"


def test_address_hash_is_sha256_of_normalized_utf8() -> None:
    raw = "  東京都  千代田区 "
    expected = hashlib.sha256("東京都 千代田区".encode("utf-8")).hexdigest()
    assert address_hash(raw) == expected


def test_address_hash_stable_across_equivalent_inputs() -> None:
    assert address_hash("東京都 千代田区") == address_hash("  東京都   千代田区  ")
    assert address_hash("１２３ 番地") == address_hash("123 番地")


def test_address_hash_distinct_for_distinct_addresses() -> None:
    assert address_hash("東京都 千代田区") != address_hash("東京都 中央区")
