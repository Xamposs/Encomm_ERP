"""Pure-Python tests for Daily Alerts data source (``load_daily_alerts``)."""

from __future__ import annotations

import os
import sqlite3
import pytest
from datetime import date, timedelta

from qt_app.data_source import (
    load_daily_alerts, AlertItem, DailyAlertsSnapshot, DailyAlertsResult,
)


def _d(offset: int) -> str:
    """Return YYYY-MM-DD relative to today."""
    return (date.today() + timedelta(days=offset)).isoformat()


def _make_db(path: str, products: list[tuple]) -> None:
    """Create ProductMaster table and seed with tuples.

    Each tuple: (barcode, name, stock, expiry_date, price).
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL,
            ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL
        )
    """)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?, ?, ?, ?, ?)", p)
    conn.commit()
    conn.close()


def _reason_set(item: AlertItem) -> set[str]:
    return set(item.reasons)


# ═══════════════════════════════════════════════════════════════════════

class TestDailyAlertsLoad:

    # ── Basic loads ────────────────────────────────────────────────────

    def test_empty_db_returns_zero_counts(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.ok
        snap = r.snapshot
        assert snap.low_stock_count == 0
        assert snap.expiring_soon_count == 0
        assert snap.expired_count == 0
        assert snap.total_alerts == 0
        assert len(snap.items) == 0

    def test_missing_db_error(self, tmp_path):
        r = load_daily_alerts(str(tmp_path / "gone.db"))
        assert not r.ok
        assert "Αδυναμία" in r.error_message
        assert r.snapshot is None
        assert not os.path.exists(str(tmp_path / "gone.db"))

    def test_invalid_filter_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "X", 5, _d(365), 1.0)])
        r = load_daily_alerts(db, alert_filter="bogus")
        assert not r.ok
        assert "bogus" in r.error_message

    # ── Low stock threshold ────────────────────────────────────────────

    def test_low_stock_detected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Low", 3, _d(365), 1.0)])
        r = load_daily_alerts(db, threshold=5, alert_days=30)
        assert r.snapshot.low_stock_count == 1
        assert r.snapshot.expired_count == 0
        assert r.snapshot.expiring_soon_count == 0
        assert r.snapshot.total_alerts == 1
        assert _reason_set(r.snapshot.items[0]) == {"Χαμηλό απόθεμα"}

    def test_stock_at_threshold_is_low(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Edge", 10, _d(365), 1.0)])
        r = load_daily_alerts(db, threshold=10)
        assert r.snapshot.low_stock_count == 1

    def test_stock_above_threshold_not_low(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Ok", 11, _d(365), 1.0)])
        r = load_daily_alerts(db, threshold=10)
        assert r.snapshot.low_stock_count == 0
        assert r.snapshot.total_alerts == 0

    # ── Expired ────────────────────────────────────────────────────────

    def test_expired_detected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Exp", 50, _d(-1), 1.0)])  # yesterday
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expired_count == 1
        assert r.snapshot.expiring_soon_count == 0
        assert _reason_set(r.snapshot.items[0]) == {"Ληγμένο"}

    def test_expiry_today_not_expired(self, tmp_path):
        """Expiry == today is NOT expired (strictly before today)."""
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Today", 50, _d(0), 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expired_count == 0

    def test_expiry_today_is_expiring_soon(self, tmp_path):
        """Expiry == today IS expiring soon (today through alert_days)."""
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Today", 50, _d(0), 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=5)
        assert r.snapshot.expiring_soon_count == 1
        assert "Λήγει σύντομα" in r.snapshot.items[0].reasons[0]

    # ── Expiring soon boundaries ───────────────────────────────────────

    def test_expiring_soon_within_window(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Near", 50, _d(15), 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expiring_soon_count == 1
        assert r.snapshot.expired_count == 0

    def test_expiring_soon_at_boundary(self, tmp_path):
        """Expiry exactly alert_days from today IS expiring soon."""
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Boundary", 50, _d(30), 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expiring_soon_count == 1

    def test_expiring_soon_beyond_window_not_alerted(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Far", 50, _d(31), 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expiring_soon_count == 0
        assert r.snapshot.expired_count == 0
        assert r.snapshot.total_alerts == 0

    # ── Blank expiry ───────────────────────────────────────────────────

    def test_blank_expiry_not_expired(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Blank", 5, "", 1.0)])
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.expired_count == 0
        assert r.snapshot.expiring_soon_count == 0
        # Low stock still applies
        assert r.snapshot.low_stock_count == 1
        assert _reason_set(r.snapshot.items[0]) == {"Χαμηλό απόθεμα"}

    # ── Combined reasons without duplicates ────────────────────────────

    def test_product_appears_once_with_all_reasons(self, tmp_path):
        """Expired + low stock product must appear once with both labels."""
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "Double", 3, _d(-5), 1.0)])  # expired + low
        r = load_daily_alerts(db, threshold=10, alert_days=30)
        assert r.snapshot.total_alerts == 1
        assert len(r.snapshot.items) == 1
        reasons = _reason_set(r.snapshot.items[0])
        assert "Ληγμένο" in reasons
        assert "Χαμηλό απόθεμα" in reasons

    def test_near_expiry_and_low_stock_combined(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "NearLow", 2, _d(10), 1.0)])
        r = load_daily_alerts(db, threshold=5, alert_days=30)
        assert r.snapshot.total_alerts == 1
        reasons = _reason_set(r.snapshot.items[0])
        assert any("Λήγει σύντομα" in s for s in reasons)
        assert "Χαμηλό απόθεμα" in reasons

    # ── Filters ────────────────────────────────────────────────────────

    def test_filter_expired_only(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("E", "Expired", 50, _d(-1), 1.0),
            ("N", "Near",    50, _d(10), 1.0),
            ("L", "Low",      3, _d(365), 1.0),
        ])
        r = load_daily_alerts(db, alert_filter="expired",
                              threshold=10, alert_days=30)
        assert r.snapshot.expired_count == 1  # still reflects global count
        assert r.snapshot.total_alerts == 1
        assert len(r.snapshot.items) == 1
        assert r.snapshot.items[0].barcode == "E"

    def test_filter_expiring_soon_only(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("E", "Exp",  50, _d(-1), 1.0),
            ("N", "Near", 50, _d(10), 1.0),
            ("L", "Low",   3, _d(365), 1.0),
        ])
        r = load_daily_alerts(db, alert_filter="expiring_soon",
                              threshold=10, alert_days=30)
        assert r.snapshot.total_alerts == 1
        assert r.snapshot.items[0].barcode == "N"

    def test_filter_low_stock_only(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("E", "Exp",    5, _d(-1), 1.0),   # expired AND low stock
            ("L", "Low",    3, _d(365), 1.0),   # just low
            ("OK", "OK",   50, _d(365), 1.0),   # nothing
        ])
        r = load_daily_alerts(db, alert_filter="low_stock",
                              threshold=10, alert_days=30)
        # Both E and L have low stock
        assert r.snapshot.total_alerts == 2
        barcodes = {i.barcode for i in r.snapshot.items}
        assert barcodes == {"E", "L"}

    def test_filter_all(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("E", "Exp",  50, _d(-1), 1.0),
            ("N", "Near", 50, _d(10), 1.0),
            ("L", "Low",   3, _d(365), 1.0),
            ("OK", "OK",  50, _d(365), 1.0),
        ])
        r = load_daily_alerts(db, alert_filter="all",
                              threshold=10, alert_days=30)
        assert r.snapshot.total_alerts == 3  # E, N, L; OK excluded
        assert len(r.snapshot.items) == 3

    # ── Ordering ───────────────────────────────────────────────────────

    def test_ordering_expired_first(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("L", "LowOnly",   3, _d(365), 1.0),
            ("E", "Expired",  50, _d(-10), 1.0),
            ("N", "Near",     50, _d(5), 1.0),
        ])
        r = load_daily_alerts(db, alert_filter="all",
                              threshold=10, alert_days=30)
        # Expected order by severity: expired, near, low
        barcodes = [i.barcode for i in r.snapshot.items]
        assert barcodes[0] == "E"  # expired
        assert barcodes[1] == "N"  # near
        assert barcodes[2] == "L"  # low only

    def test_ordering_same_severity_by_expiry_then_name(self, tmp_path):
        """Two expired products: earlier expiry first; if tie, name asc."""
        db = str(tmp_path / "test.db")
        _make_db(db, [
            ("B", "Beta",  50, _d(-5), 1.0),
            ("A", "Alpha", 50, _d(-5), 1.0),  # same day, earlier alphabetically
            ("C", "Gamma", 50, _d(-1), 1.0),  # later expiry
        ])
        r = load_daily_alerts(db, alert_filter="expired",
                              threshold=10, alert_days=30)
        items = r.snapshot.items
        # Earlier expiry first (d(-5) before d(-1))
        assert items[0].expiry_date == _d(-5)
        assert items[0].barcode == "A"  # Alpha before Beta
        assert items[1].barcode == "B"
        assert items[2].barcode == "C"

    # ── Pagination ─────────────────────────────────────────────────────

    def test_pagination_clamps(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            (str(i), f"P{i}", 1, _d(365), 1.0) for i in range(25)
        ])
        r = load_daily_alerts(db, threshold=10, page=2, page_size=10)
        assert r.ok
        assert r.snapshot.page == 2
        assert len(r.snapshot.items) == 10
        assert r.snapshot.total_alerts == 25

    def test_page_beyond_total_clamps(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [
            (str(i), f"P{i}", 1, _d(365), 1.0) for i in range(5)
        ])
        r = load_daily_alerts(db, threshold=10, page=999, page_size=10)
        assert r.ok
        assert r.snapshot.page == 1  # clamped to last page

    def test_page_zero_clamped_to_one(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "X", 1, _d(365), 1.0)])
        r = load_daily_alerts(db, threshold=10, page=0, page_size=10)
        assert r.snapshot.page == 1

    def test_page_size_zero_clamped_to_one(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "X", 1, _d(365), 1.0)])
        r = load_daily_alerts(db, threshold=10, page_size=0)
        assert r.ok
        assert 0 < len(r.snapshot.items) <= 100

    # ── Read-only safety ───────────────────────────────────────────────

    def test_no_writes_on_repeat_calls(self, tmp_path):
        """Repeated reads must not alter the database."""
        db = str(tmp_path / "test.db")
        _make_db(db, [("A", "X", 5, _d(365), 1.0)])
        for _ in range(3):
            r = load_daily_alerts(db, threshold=10)
            assert r.ok
        # Verify DB is unchanged
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        count = conn.execute("SELECT COUNT(*) FROM ProductMaster").fetchone()[0]
        conn.close()
        assert count == 1
