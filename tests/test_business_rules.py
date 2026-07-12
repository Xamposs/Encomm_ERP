"""Tests for core/business_rules.py."""
from datetime import date, timedelta

import pytest

from core.business_rules import (
    calculate_invoice_totals,
    calculate_vat,
    get_days_until_expiry,
    get_vat_rate,
    is_expired,
    is_low_stock,
    is_near_expiry,
    is_valid_ean13,
    parse_date,
)
from core.domain_models import Product


# ── VAT / totals ────────────────────────────────────────────────────

def test_calculate_vat_rounds_to_two_dp():
    assert calculate_vat(100, 0.15) == 15.0
    assert calculate_vat(3.33, 0.24) == 0.80  # 0.7992 → 0.80


def test_get_vat_rate_maps_pharmacy_tiers():
    assert get_vat_rate(6) == 0.06
    assert get_vat_rate(13) == 0.13
    assert get_vat_rate(24) == 0.24


def test_get_vat_rate_rejects_invalid_category():
    with pytest.raises(ValueError):
        get_vat_rate(20)


def test_calculate_invoice_totals():
    items = [
        (Product("b1", "A", 10, "2099-01-01", 5.0), 2),   # 10.00
        (Product("b2", "B", 10, "2099-01-01", 2.5), 4),   # 10.00
    ]
    vat, grand = calculate_invoice_totals(items, 0.15)
    # subtotal = 20.00, vat = 3.00, grand = 23.00
    assert vat == 3.00
    assert grand == 23.00


# ── parse_date ──────────────────────────────────────────────────────

def test_parse_date_valid():
    assert parse_date("2099-12-31") == date(2099, 12, 31)


def test_parse_date_strips_timestamp():
    assert parse_date("2099-12-31 23:59:59") == date(2099, 12, 31)


def test_parse_date_malformed_returns_far_future_and_warns(caplog):
    with caplog.at_level("WARNING"):
        result = parse_date("not-a-date")
    assert result == date.max
    assert any("Unparseable expiry" in r.message for r in caplog.records)


# ── expiry helpers ──────────────────────────────────────────────────

def _product(expiry_offset_days):
    p = Product("b1", "A", 10, "2099-01-01", 1.0)
    p.expiry_date = (date.today() + timedelta(days=expiry_offset_days)).isoformat()
    return p


def test_is_expired_true():
    assert is_expired(_product(-5)) is True


def test_is_expired_false_for_future():
    assert is_expired(_product(60)) is False


def test_is_near_expiry_within_threshold():
    assert is_near_expiry(_product(10), threshold_days=30) is True


def test_is_near_expiry_outside_threshold():
    assert is_near_expiry(_product(60), threshold_days=30) is False


def test_is_near_expiry_not_already_expired():
    # A product already expired should NOT count as "near expiry".
    assert is_near_expiry(_product(-5), threshold_days=30) is False


def test_get_days_until_expiry():
    assert get_days_until_expiry(_product(7)) == 7


# ── low stock ───────────────────────────────────────────────────────

def test_is_low_stock_at_threshold():
    assert is_low_stock(Product("b", "n", 10, "2099-01-01", 1.0), threshold=10) is True


def test_is_low_stock_above_threshold():
    assert is_low_stock(Product("b", "n", 11, "2099-01-01", 1.0), threshold=10) is False


# ── EAN-13 ──────────────────────────────────────────────────────────

def test_is_valid_ean13_valid():
    # 5200000000016 is a valid EAN-13 (correct check digit).
    assert is_valid_ean13("5200000000016") is True


def test_is_valid_ean13_bad_checksum():
    # Same digits but wrong check digit (7 instead of 6).
    assert is_valid_ean13("5200000000017") is False


def test_is_valid_ean13_wrong_length():
    assert is_valid_ean13("123") is False
    assert is_valid_ean13("12345678901234") is False


def test_is_valid_ean13_non_digit():
    assert is_valid_ean13("520000000001a") is False


def test_is_valid_ean13_none_and_non_str():
    assert is_valid_ean13(None) is False
    assert is_valid_ean13(123) is False
