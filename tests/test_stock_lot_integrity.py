"""Focused tests for stock-lot expiry integrity model (Phase P5.2a).

Tests validate ``load_stock_lot_integrity()`` — a pure, read-only,
deterministic snapshot of stock-lot tracking readiness.  Every test
creates its own temporary SQLite database and cleans up.

Coverage requirements
---------------------
- stock_lots table absent
- empty stock_lots table
- multiple lots for one barcode with different expiry dates
- expired, expiring-soon and future lots
- blank expiry date
- malformed and impossible dates
- zero-quantity lots ignored
- master stock equal to lot total
- master stock greater than lot total
- lot total greater than master stock
- product with stock but no lots
- product with zero master stock but positive lot discrepancy
- exact boundary dates for business_date and alert_days
- deterministic ordering
- aggregate totals
- query-only behavior
- no database schema or row mutation
- no reliance on ProductMaster.ExpiryDate
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pytest

from infrastructure.stock_lot_integrity_model import (
    load_stock_lot_integrity,
    ProductLotIntegrity,
    StockLotIntegrityResult,
    StockLotIntegritySnapshot,
    _validate_date_str,
    _classify_product,
)


# ═══════════════════════════════════════════════════════════════════════
# Test helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_db(path: str) -> sqlite3.Connection:
    """Create a fresh SQLite DB with ProductMaster and stock_lots tables."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL CHECK(Stock >= 0),
            ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL CHECK(Price >= 0)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_lots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode      TEXT NOT NULL,
            batch_number TEXT NOT NULL DEFAULT '',
            expiry_date  TEXT NOT NULL DEFAULT '',
            quantity     INTEGER NOT NULL CHECK(quantity >= 0),
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            FOREIGN KEY (barcode) REFERENCES ProductMaster(Barcode),
            UNIQUE (barcode, batch_number, expiry_date)
        )
    """)
    conn.row_factory = sqlite3.Row
    return conn


def _add_product(conn, barcode: str, name: str, stock: int,
                 expiry_date: str = "2099-12-31", price: float = 5.0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, ?, ?)",
        (barcode, name, stock, expiry_date, price),
    )


def _add_lot(conn, barcode: str, qty: int, expiry_date: str,
             batch: str = "BATCH1") -> None:
    conn.execute(
        "INSERT INTO stock_lots (barcode, batch_number, expiry_date, quantity, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2025-01-01 00:00:00', '2025-01-01 00:00:00')",
        (barcode, batch, expiry_date, qty),
    )


def _d(offset: int, base: date | None = None) -> str:
    """Return YYYY-MM-DD relative to *base* or today."""
    b = base or date.today()
    return (b + timedelta(days=offset)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_empty(path: str) -> str:
    """Create an empty SQLite file (just the file, no tables)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    return path


# ═══════════════════════════════════════════════════════════════════════
# Public date validator
# ═══════════════════════════════════════════════════════════════════════

class TestDateValidator:

    def test_valid_dates(self):
        assert _validate_date_str("2026-01-15") is not None
        assert _validate_date_str("2026-12-31") is not None
        assert _validate_date_str("2026-02-28") is not None
        assert _validate_date_str("2024-02-29") is not None  # leap year

    def test_invalid_dates(self):
        assert _validate_date_str("") is None
        assert _validate_date_str("not-a-date") is None
        assert _validate_date_str("2026-13-01") is None   # month > 12
        assert _validate_date_str("2026-00-15") is None   # month < 1
        assert _validate_date_str("2026-01-32") is None   # day > 31
        assert _validate_date_str("2026-02-30") is None   # Feb 30
        assert _validate_date_str("2023-02-29") is None   # non-leap Feb 29
        assert _validate_date_str("26-01-15") is None     # short year
        assert _validate_date_str("2026/01/15") is None   # wrong separator

    def test_blank_classified_as_none(self):
        assert _validate_date_str("") is None


# ═══════════════════════════════════════════════════════════════════════
# Business-date validation errors
# ═══════════════════════════════════════════════════════════════════════

class TestBusinessDateValidation:

    def test_invalid_business_date(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="not-a-date")
        assert not r.ok
        assert "YYYY-MM-DD" in r.error_message

    def test_negative_alert_days(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=-1)
        assert not r.ok
        assert "alert_days" in r.error_message.lower()

    def test_non_int_alert_days(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days="30")
        assert not r.ok

    def test_bool_alert_days_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=True)
        assert not r.ok
        r2 = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=False)
        assert not r2.ok

    def test_float_alert_days_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=30.5)
        assert not r.ok

    def test_business_date_none_returns_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date=None)
        assert not r.ok
        assert r.snapshot is None

    def test_business_date_int_returns_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date=20260601)
        assert not r.ok
        assert r.snapshot is None

    def test_cutoff_overflow_returns_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="9999-12-31", alert_days=1)
        assert not r.ok
        assert "υπερχείλισε" in r.error_message

    def test_large_alert_days_returns_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=10**9)
        assert not r.ok

    def test_zero_alert_days_is_valid(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()
        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=0)
        assert r.ok

    def test_no_exception_for_any_input(self, tmp_path):
        """All invalid inputs must return StockLotIntegrityResult with ok=False."""
        db = str(tmp_path / "test.db")
        _make_db(db)
        for bd, ad in [
            (None, 30),
            ("not-a-date", 30),
            ("2026-06-01", True),
            ("2026-06-01", False),
            ("2026-06-01", -5),
            ("2026-06-01", "30"),
            ("2026-06-01", 30.0),
            ("9999-12-31", 1),
            ("2026-06-01", 10**9),
        ]:
            r = load_stock_lot_integrity(db, business_date=bd, alert_days=ad)
            assert not r.ok, f"Expected failure for bd={bd!r} ad={ad!r}"
            assert r.snapshot is None


# ═══════════════════════════════════════════════════════════════════════
# stock_lots table absent
# ═══════════════════════════════════════════════════════════════════════

class TestStockLotsTableAbsent:

    def test_no_stock_lots_table_returns_unavailable(self, tmp_path):
        """Only ProductMaster exists — stock_lots was never created."""
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
                Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL, Price REAL NOT NULL
            )
        """)
        conn.execute("INSERT INTO ProductMaster VALUES ('A', 'Test', 10, '2099-12-31', 5.0)")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        snap = r.snapshot
        assert not snap.tracking.available
        assert "stock_lots" in snap.tracking.reason.lower()
        assert snap.total_products_with_stock == 0
        assert len(snap.per_product) == 0

        # Verify stock_lots was NOT created by the query
        conn2 = sqlite3.connect(db)
        tables = {
            r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn2.close()
        assert "stock_lots" not in tables

    def test_missing_db_returns_error(self, tmp_path):
        db = str(tmp_path / "nonexistent.db")
        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert not r.ok
        assert r.snapshot is None


# ═══════════════════════════════════════════════════════════════════════
# Empty stock_lots table
# ═══════════════════════════════════════════════════════════════════════

class TestEmptyStockLots:

    def test_empty_stock_lots_shows_all_as_untracked(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_product(conn, "B", "Beta", 30)
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        snap = r.snapshot
        assert snap.tracking.available
        assert len(snap.per_product) == 2
        assert snap.total_products_with_stock == 2

        for p in snap.per_product:
            assert p.total_lot_qty == 0
            assert p.untracked_qty == p.master_stock
            assert p.status == "Απαρακολούθητο Απόθεμα"


# ═══════════════════════════════════════════════════════════════════════
# Multiple lots for one barcode with different expiry dates
# ═══════════════════════════════════════════════════════════════════════

class TestMultipleLotsOneBarcode:

    def test_multiple_lots_aggregated_correctly(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 100)
        _add_lot(conn, "A", 30, "2026-12-01", batch="B1")
        _add_lot(conn, "A", 40, "2027-06-15", batch="B2")
        _add_lot(conn, "A", 20, "2028-01-01", batch="B3")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 90  # 30 + 40 + 20
        assert p.qty_in_dated_lots == 90
        assert p.untracked_qty == 10  # 100 - 90
        assert p.master_stock == 100
        assert p.earliest_valid_expiry == "2026-12-01"


# ═══════════════════════════════════════════════════════════════════════
# Expired, expiring-soon, and future lots
# ═══════════════════════════════════════════════════════════════════════

class TestExpiryClassification:

    def test_expired_lots_detected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 20, "2025-01-01")  # expired
        _add_lot(conn, "A", 30, "2026-12-01")  # future
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expired_lot_qty == 20
        assert p.future_lot_qty == 30
        assert p.expiring_soon_lot_qty == 0
        assert p.status == "Ληγμένο"

    def test_expiring_soon_lots_detected(self, tmp_path):
        """Test with a known business_date to get deterministic behaviour."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        # With business_date=2026-06-15, alert_days=30 → cutoff=2026-07-15
        _add_lot(conn, "A", 15, "2026-07-10")  # expiring soon
        _add_lot(conn, "A", 35, "2027-01-01")  # far future
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15", alert_days=30)
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expiring_soon_lot_qty == 15
        assert p.future_lot_qty == 35
        assert p.expired_lot_qty == 0
        assert p.status == "Λήγει Σύντομα"

    def test_future_only_lots(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")  # far future
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expired_lot_qty == 0
        assert p.expiring_soon_lot_qty == 0
        assert p.future_lot_qty == 50
        assert p.status == "Πλήρως Καταγεγραμμένο"


# ═══════════════════════════════════════════════════════════════════════
# Blank expiry date
# ═══════════════════════════════════════════════════════════════════════

class TestBlankExpiryDate:

    def test_blank_expiry_is_undated(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 20, "")  # blank expiry
        _add_lot(conn, "A", 30, "2027-06-01")  # dated
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.qty_in_undated_lots == 20
        assert p.qty_in_dated_lots == 30
        assert p.qty_in_invalid_date_lots == 0
        # Lots total (50) == master stock (50), but 20 units are undated
        assert p.status == "Αχρονολόγητες Παρτίδες"


# ═══════════════════════════════════════════════════════════════════════
# Malformed and impossible dates
# ═══════════════════════════════════════════════════════════════════════

class TestInvalidDates:

    def test_malformed_date_is_invalid(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 25, "not-a-date")  # malformed
        _add_lot(conn, "A", 25, "2027-06-01")  # valid
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.qty_in_invalid_date_lots == 25
        assert p.qty_in_dated_lots == 25
        assert p.status == "Μη Έγκυρη Ημερομηνία"

    def test_impossible_date_is_invalid(self, tmp_path):
        """Feb 30 normalises to Mar 2 — check that our regex catches it before SQLite."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 10, "2026-02-30")  # impossible — Feb has 28/29 days
        _add_lot(conn, "A", 40, "2027-01-01")  # valid
        conn.commit()
        conn.close()

        # SQLite date('2026-02-30') gives '2026-03-02' ≠ '2026-02-30' → invalid
        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        # date('2026-02-30') → '2026-03-02' which != '2026-02-30' → invalid
        assert p.qty_in_invalid_date_lots == 10
        assert p.status == "Μη Έγκυρη Ημερομηνία"


# ═══════════════════════════════════════════════════════════════════════
# Zero-quantity lots ignored
# ═══════════════════════════════════════════════════════════════════════

class TestZeroQuantityLotsIgnored:

    def test_zero_qty_lot_not_counted(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 0, "2027-01-01", batch="ZERO")  # zero qty
        _add_lot(conn, "A", 50, "2027-06-01", batch="REAL")  # real lot
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 50  # zero-qty lot excluded
        assert p.future_lot_qty == 50
        assert p.status == "Πλήρως Καταγεγραμμένο"

    def test_only_zero_qty_lots_means_no_lots(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 0, "2027-01-01", batch="ZERO1")
        _add_lot(conn, "A", 0, "2027-06-01", batch="ZERO2")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 0
        assert p.untracked_qty == 50
        assert p.status == "Απαρακολούθητο Απόθεμα"


# ═══════════════════════════════════════════════════════════════════════
# Master stock equal to lot total
# ═══════════════════════════════════════════════════════════════════════

class TestMasterEqualsLotTotal:

    def test_full_coverage(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 30, "2027-06-01")
        _add_lot(conn, "A", 20, "2028-01-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 50
        assert p.master_stock == 50
        assert p.untracked_qty == 0
        assert p.lot_overage_qty == 0
        assert p.status == "Πλήρως Καταγεγραμμένο"


# ═══════════════════════════════════════════════════════════════════════
# Master stock greater than lot total
# ═══════════════════════════════════════════════════════════════════════

class TestMasterGreaterThanLotTotal:

    def test_untracked_stock(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 100)
        _add_lot(conn, "A", 30, "2027-06-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 30
        assert p.untracked_qty == 70
        assert p.lot_overage_qty == 0
        assert p.status == "Απαρακολούθητο Απόθεμα"


# ═══════════════════════════════════════════════════════════════════════
# Lot total greater than master stock
# ═══════════════════════════════════════════════════════════════════════

class TestLotTotalGreaterThanMaster:

    def test_lot_overage_detected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 60, "2027-06-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.total_lot_qty == 60
        assert p.lot_overage_qty == 10
        assert p.untracked_qty == 0
        assert p.status == "Λάθος: Υπερβολική Ποσότητα Παρτίδας"


# ═══════════════════════════════════════════════════════════════════════
# Product with stock but no lots
# ═══════════════════════════════════════════════════════════════════════

class TestProductWithStockNoLots:

    def test_stock_with_no_lots_is_untracked(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 75)
        # No stock_lots inserted for this product
        _add_lot(conn, "B", 10, "2027-01-01")  # different product
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        # Find product A
        p = next(x for x in r.snapshot.per_product if x.barcode == "A")
        assert p.total_lot_qty == 0
        assert p.untracked_qty == 75
        assert p.status == "Απαρακολούθητο Απόθεμα"


# ═══════════════════════════════════════════════════════════════════════
# Product with zero master stock but positive lot discrepancy
# ═══════════════════════════════════════════════════════════════════════

class TestZeroStockPositiveLot:

    def test_zero_stock_with_lot_is_overage(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 0)
        _add_lot(conn, "A", 20, "2027-06-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        assert len(r.snapshot.per_product) == 1
        p = r.snapshot.per_product[0]
        assert p.master_stock == 0
        assert p.total_lot_qty == 20
        assert p.lot_overage_qty == 20
        assert p.status == "Λάθος: Υπερβολική Ποσότητα Παρτίδας"
        assert r.snapshot.total_products_with_stock == 0  # master 0 doesn't count


# ═══════════════════════════════════════════════════════════════════════
# Exact boundary dates for business_date and alert_days
# ═══════════════════════════════════════════════════════════════════════

class TestBoundaryDates:

    def test_exactly_expired_on_business_date(self, tmp_path):
        """Lot with expiry == business_date should NOT be expired (not before)."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 20, "2026-06-15")  # equals business_date
        _add_lot(conn, "A", 30, "2027-01-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expired_lot_qty == 0  # not < business_date
        assert p.expiring_soon_lot_qty == 20  # >= business_date and <= cutoff

    def test_exactly_cutoff_date_is_expiring(self, tmp_path):
        """Lot with expiry == cutoff should be expiring soon."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        # With business_date=2026-06-01, alert_days=30 → cutoff=2026-07-01
        _add_lot(conn, "A", 20, "2026-07-01")  # equals cutoff
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=30)
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expiring_soon_lot_qty == 20
        assert p.status == "Λήγει Σύντομα"

    def test_one_day_before_business_date_is_expired(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 20, "2026-05-31")  # day before business_date
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-01")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expired_lot_qty == 20

    def test_one_past_cutoff_is_future(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        # cutoff = 2026-07-01, so 2026-07-02 is future
        _add_lot(conn, "A", 20, "2026-07-02")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-01", alert_days=30)
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.future_lot_qty == 20
        assert p.expiring_soon_lot_qty == 0


# ═══════════════════════════════════════════════════════════════════════
# Deterministic ordering
# ═══════════════════════════════════════════════════════════════════════

class TestDeterministicOrdering:

    def test_ordering_by_severity(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        # Products with different statuses
        _add_product(conn, "OVER", "Overage", 50)
        _add_lot(conn, "OVER", 60, "2027-01-01")  # overage

        _add_product(conn, "EXP", "Expired", 50)
        _add_lot(conn, "EXP", 20, "2025-01-01")  # expired

        _add_product(conn, "INV", "Invalid Date", 50)
        _add_lot(conn, "INV", 10, "bad-date")  # invalid

        _add_product(conn, "SOON", "Expiring Soon", 50)
        _add_lot(conn, "SOON", 15, "2026-07-10")  # expiring soon (bd=2026-06-15, cutoff=2026-07-15)

        _add_product(conn, "UNTR", "Untracked", 50)  # no lots

        _add_product(conn, "UNDT", "Undated", 50)
        _add_lot(conn, "UNDT", 50, "")  # undated

        _add_product(conn, "FULL", "Full", 50)
        _add_lot(conn, "FULL", 50, "2027-06-01")  # fully tracked

        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15", alert_days=30)
        assert r.ok
        statuses = [p.status for p in r.snapshot.per_product]
        assert statuses[0].startswith("Λάθος")
        assert statuses[1] == "Ληγμένο"
        assert statuses[2] == "Μη Έγκυρη Ημερομηνία"
        assert statuses[3] == "Λήγει Σύντομα"
        assert statuses[4] == "Απαρακολούθητο Απόθεμα"
        assert statuses[5] == "Αχρονολόγητες Παρτίδες"
        assert statuses[6] == "Πλήρως Καταγεγραμμένο"

    def test_within_same_severity_ordered_by_expiry_then_name_then_barcode(
        self, tmp_path,
    ):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "C", "Alpha", 50)
        _add_lot(conn, "C", 50, "2027-03-01")  # Mar expiry
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-01-01")  # Jan expiry
        _add_product(conn, "B", "Beta", 50)
        _add_lot(conn, "B", 50, "2027-01-01")  # Jan expiry, Beta > Alpha
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        barcodes = [p.barcode for p in r.snapshot.per_product]
        # A (Alpha, Jan) → B (Beta, Jan) — same expiry, name determines order
        # then C (Alpha, Mar) — later expiry
        assert barcodes == ["A", "B", "C"]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate totals
# ═══════════════════════════════════════════════════════════════════════

class TestAggregateTotals:

    def test_aggregates_reflect_all_products(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        # A — fully covered, far future (fully_covered + future only)
        _add_product(conn, "A", "Alpha Full", 100)
        _add_lot(conn, "A", 100, "2027-06-01")

        # B — no lots (untracked only)
        _add_product(conn, "B", "Beta Untracked", 50)

        # C — undated only (undated_lot_products)
        _add_product(conn, "C", "Gamma Undated", 30)
        _add_lot(conn, "C", 30, "")

        # D — expired + undated + untracked: master=40, lots=10(e)+10(u)+15(f)=35
        #     untracked = 5 → counts as ALL THREE: expired_lot_units, undated_lot_products,
        #     untracked_products
        _add_product(conn, "D", "Delta Mixed", 40)
        _add_lot(conn, "D", 10, "2025-01-01")  # expired
        _add_lot(conn, "D", 10, "")            # undated
        _add_lot(conn, "D", 15, "2027-01-01")  # future

        # E — overage (lot_overage_products)
        _add_product(conn, "E", "Epsilon Overage", 10)
        _add_lot(conn, "E", 20, "2027-01-01")

        # F — fully covered by dated lots but CONTAINS expired units
        #     master=50, lots=10(e expired)+40(f future)=50, untracked=0,
        #     undated=0, invalid=0, dated=50.  qty_in_dated_lots == master_stock
        #     → fully_covered=TRUE; expired_lot_units also counts
        _add_product(conn, "F", "Zeta Full But Expired", 50)
        _add_lot(conn, "F", 10, "2025-01-01")  # expired
        _add_lot(conn, "F", 40, "2027-06-01")  # future

        # G — fully covered by dated lots but expires SOON
        #     master=60, lots=20(expiring)+40(future)=60, untracked=0
        #     → fully_covered=TRUE; expiring_soon_lot_units also counts
        _add_product(conn, "G", "Eta Full But Expiring", 60)
        _add_lot(conn, "G", 20, "2026-07-10")  # expiring (bd=2026-06-15, cutoff=2026-07-15)
        _add_lot(conn, "G", 40, "2027-06-01")  # future

        # H — invalid dates + untracked stock (counts in BOTH aggregates)
        #     master=100, lots=15(invalid)+50(future)=65, untracked=35
        _add_product(conn, "H", "Theta Invalid Untracked", 100)
        _add_lot(conn, "H", 15, "bad-date")   # invalid
        _add_lot(conn, "H", 50, "2027-06-01")  # future

        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        snap = r.snapshot

        assert snap.total_products_with_stock == 8  # all 8 have master > 0
        assert snap.fully_covered == 3               # A, F, G
        assert snap.untracked_products == 3          # B, D, H
        assert snap.undated_lot_products == 2        # C, D
        assert snap.lot_overage_products == 1        # E
        assert snap.invalid_date_products == 1       # H
        assert snap.expired_lot_units == 20          # D(10) + F(10)
        assert snap.expiring_soon_lot_units == 20    # G(20)

        # Verify product F has primary status "Ληγμένο" despite being fully_covered
        p_f = next(p for p in snap.per_product if p.barcode == "F")
        assert p_f.status == "Ληγμένο"
        # Verify product G has primary status "Λήγει Σύντομα" despite being fully_covered
        p_g = next(p for p in snap.per_product if p.barcode == "G")
        assert p_g.status == "Λήγει Σύντομα"
        # Verify H has primary status "Μη Έγκυρη Ημερομηνία" (higher severity than untracked)
        p_h = next(p for p in snap.per_product if p.barcode == "H")
        assert p_h.status == "Μη Έγκυρη Ημερομηνία"


# ═══════════════════════════════════════════════════════════════════════
# Query-only behavior — no writes, no schema changes
# ═══════════════════════════════════════════════════════════════════════

class TestQueryOnly:

    def test_no_schema_changes(self, tmp_path):
        """Database schema must be identical before and after the call."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()

        # Capture complete schema definitions before
        defs_before = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        ).fetchall()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok

        conn2 = sqlite3.connect(db)
        conn2.row_factory = sqlite3.Row
        defs_after = conn2.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        ).fetchall()
        conn2.close()

        assert len(defs_before) == len(defs_after)
        for b, a in zip(defs_before, defs_after):
            assert tuple(b) == tuple(a), (
                f"Schema changed: {dict(b)} → {dict(a)}"
            )

    def test_no_row_mutations(self, tmp_path):
        """Row data must be unchanged after the call — compare full content."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()

        # Capture full ProductMaster rows before
        pm_before = [
            tuple(r) for r in conn.execute(
                "SELECT * FROM ProductMaster ORDER BY Barcode"
            ).fetchall()
        ]
        lot_before = [
            tuple(r) for r in conn.execute(
                "SELECT * FROM stock_lots ORDER BY id"
            ).fetchall()
        ]
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok

        conn2 = sqlite3.connect(db)
        conn2.row_factory = sqlite3.Row
        pm_after = [
            tuple(r) for r in conn2.execute(
                "SELECT * FROM ProductMaster ORDER BY Barcode"
            ).fetchall()
        ]
        lot_after = [
            tuple(r) for r in conn2.execute(
                "SELECT * FROM stock_lots ORDER BY id"
            ).fetchall()
        ]
        conn2.close()

        assert pm_before == pm_after, "ProductMaster rows changed!"
        assert lot_before == lot_after, "stock_lots rows changed!"

    def test_no_reliance_on_productmaster_expirydate(self, tmp_path):
        """The model should work correctly even if ProductMaster.ExpiryDate
        is different from lot expiry dates."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        # Product has ExpiryDate far in the future, but lots tell a different story
        _add_product(conn, "A", "Alpha", 50, expiry_date="2099-12-31")
        _add_lot(conn, "A", 20, "2025-01-01")  # expired
        _add_lot(conn, "A", 30, "2027-06-01")
        conn.commit()
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok
        p = r.snapshot.per_product[0]
        assert p.expired_lot_qty == 20  # detected via lot expiry, not ProductMaster


# ═══════════════════════════════════════════════════════════════════════
# _classify_product helper (unit tests)
# ═══════════════════════════════════════════════════════════════════════

class TestClassifyProduct:

    def test_overage_wins_over_expired(self):
        status, _ = _classify_product(
            master_stock=10, total_lot=20, qty_dated=20,
            qty_undated=0, qty_invalid=0, qty_expired=5,
            qty_expiring=0, qty_future=15, lot_overage=10,
        )
        assert "Υπερβολική" in status

    def test_expired_wins_over_invalid(self):
        status, _ = _classify_product(
            master_stock=50, total_lot=40, qty_dated=20,
            qty_undated=0, qty_invalid=10, qty_expired=10,
            qty_expiring=0, qty_future=10, lot_overage=0,
        )
        assert "Ληγμένο" in status

    def test_invalid_wins_over_expiring(self):
        status, _ = _classify_product(
            master_stock=50, total_lot=40, qty_dated=20,
            qty_undated=0, qty_invalid=10, qty_expired=0,
            qty_expiring=10, qty_future=10, lot_overage=0,
        )
        assert "Μη Έγκυρη" in status

    def test_expiring_wins_over_untracked(self):
        status, _ = _classify_product(
            master_stock=100, total_lot=60, qty_dated=30,
            qty_undated=0, qty_invalid=0, qty_expired=0,
            qty_expiring=15, qty_future=15, lot_overage=0,
        )
        assert "Λήγει Σύντομα" in status

    def test_untracked_wins_over_undated(self):
        status, _ = _classify_product(
            master_stock=100, total_lot=40, qty_dated=10,
            qty_undated=30, qty_invalid=0, qty_expired=0,
            qty_expiring=0, qty_future=10, lot_overage=0,
        )
        assert "Απαρακολούθητο" in status

    def test_undated_only(self):
        status, _ = _classify_product(
            master_stock=50, total_lot=50, qty_dated=0,
            qty_undated=50, qty_invalid=0, qty_expired=0,
            qty_expiring=0, qty_future=0, lot_overage=0,
        )
        assert "Αχρονολόγητες" in status

    def test_fully_tracked(self):
        status, _ = _classify_product(
            master_stock=50, total_lot=50, qty_dated=50,
            qty_undated=0, qty_invalid=0, qty_expired=0,
            qty_expiring=0, qty_future=50, lot_overage=0,
        )
        assert "Πλήρως Καταγεγραμμένο" in status
