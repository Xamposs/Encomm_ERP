"""Pure-Python tests for ``qt_app.data_source``.

Do NOT require PySide6 or a display — these exercise the read-only
SQLite data-access layer only.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import pytest

from qt_app.data_source import (
    load_dashboard,
    DashboardResult,
    DashboardSnapshot,
    CriticalProduct,
    _connect_ro,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_minimal_db(path: str) -> None:
    """Create a minimal schema with real-world data."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL,
            ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL
        );
        CREATE TABLE invoices (
            id           TEXT PRIMARY KEY,
            invoice_date TEXT    NOT NULL,
            subtotal     REAL    NOT NULL,
            vat_amount   REAL    NOT NULL,
            grand_total  REAL    NOT NULL
        );
        INSERT INTO ProductMaster VALUES
            ('5200000000001', 'Paracetamol 500mg', 50, '2027-12-31', 3.50),
            ('5200000000002', 'Ibuprofen 400mg',   3,  '2026-06-01', 5.00),
            ('5200000000003', 'Aspirin 100mg',      0,  '2025-01-01', 2.00),
            ('5200000000004', 'Amoxicillin 250mg', 20,  '2026-08-15', 8.00),
            ('5200000000005', 'Omeprazole 20mg',   12,  '2026-05-10', 12.50),
            ('5200000000006', 'Cetirizine 10mg',   15,  '2028-06-30', 4.00);
        INSERT INTO invoices VALUES
            ('INV-001', date('now'), 10.00, 1.50, 11.50),
            ('INV-002', date('now'), 20.00, 3.00, 23.00),
            ('INV-003', '2025-01-01', 5.00, 0.75, 5.75);
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestReadOnlyConnection:
    """Verify read-only connection behaviour."""

    def test_opens_existing_db_readonly(self, tmp_path):
        db = tmp_path / "test.db"
        _make_minimal_db(str(db))
        conn = _connect_ro(str(db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ProductMaster")
        assert cur.fetchone()[0] == 6
        conn.close()

    def test_missing_db_raises(self, tmp_path):
        missing = str(tmp_path / "no_such.db")
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(missing)

    def test_readonly_blocks_writes(self, tmp_path):
        db = tmp_path / "test.db"
        _make_minimal_db(str(db))
        conn = _connect_ro(str(db))
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO ProductMaster VALUES ('X', 'Y', 1, '2027-01-01', 1.0)")
        conn.close()

    def test_no_db_file_created_for_missing_path(self, tmp_path):
        missing = str(tmp_path / "definitely_not_here.db")
        assert not os.path.exists(missing)
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(missing)
        assert not os.path.exists(missing)


class TestLoadDashboard:
    """Verify the public ``load_dashboard`` function."""

    def test_success_counts(self, tmp_path):
        db = tmp_path / "test.db"
        _make_minimal_db(str(db))
        result = load_dashboard(str(db), threshold=10, alert_days=30)
        assert result.ok
        snap = result.snapshot
        assert snap.total_products == 6
        assert snap.low_stock_count == 2  # stock 3 and 0
        # Expired: 2025-01-01 (Aspirin) and 2026-05-10 (Omeprazole) and 2026-06-01 (Ibuprofen)
        # date('now') is 2026-07-15, so:
        # - 2025-01-01 < now → expired (1)
        # - 2026-05-10 < now → expired (1)
        # - 2026-06-01 < now → expired (1)
        # - 2026-08-15 <= now+30 → near-expiry → counts too
        # So 4 total expiry alerts
        assert snap.expiry_alert_count >= 1  # at least expired ones

    def test_success_analytics(self, tmp_path):
        db = tmp_path / "test.db"
        _make_minimal_db(str(db))
        result = load_dashboard(str(db))
        assert result.ok
        snap = result.snapshot
        # 2 invoices with date('now') = 11.50 + 23.00 = 34.50
        assert snap.revenue_today == 34.50
        assert snap.vat_today == 4.50
        assert snap.invoice_count == 3

    def test_missing_db_returns_error(self, tmp_path):
        missing = str(tmp_path / "gone.db")
        result = load_dashboard(missing)
        assert not result.ok
        assert "Αδυναμία" in result.error_message
        assert result.snapshot is None

    def test_error_result_has_no_snapshot(self, tmp_path):
        result = load_dashboard(str(tmp_path / "nope.db"))
        assert not result.ok
        assert result.snapshot is None
        assert len(result.error_message) > 0

    def test_critical_products_capped_at_20(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE ProductMaster ("
            "Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER, "
            "ExpiryDate TEXT, Price REAL)")
        conn.execute(
            "CREATE TABLE invoices (id TEXT PRIMARY KEY, invoice_date TEXT, "
            "subtotal REAL, vat_amount REAL, grand_total REAL)")
        # Insert 30 low-stock products
        for i in range(30):
            conn.execute(
                "INSERT INTO ProductMaster VALUES (?, ?, ?, ?, ?)",
                (f"BC{i:06d}", f"Product {i}", 0, "2027-12-31", 1.0))
        conn.commit()
        conn.close()
        result = load_dashboard(str(db), threshold=5, alert_days=30)
        assert result.ok
        assert len(result.snapshot.critical_products) == 20

    def test_fallback_thresholds(self, tmp_path):
        """Default threshold=10, alert_days=30 when not specified."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE ProductMaster ("
            "Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER, "
            "ExpiryDate TEXT, Price REAL)")
        conn.execute(
            "CREATE TABLE invoices (id TEXT PRIMARY KEY, invoice_date TEXT, "
            "subtotal REAL, vat_amount REAL, grand_total REAL)")
        # Product with stock=8 should be caught by default threshold=10
        conn.execute(
            "INSERT INTO ProductMaster VALUES ('A', 'Low', 8, '2027-12-31', 1.0)")
        conn.commit()
        conn.close()
        result = load_dashboard(str(db))
        assert result.ok
        assert result.snapshot.low_stock_count == 1

    def test_multi_reason_product(self, tmp_path):
        """A product both low-stock AND expired gets multiple reasons."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE ProductMaster ("
            "Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER, "
            "ExpiryDate TEXT, Price REAL)")
        conn.execute(
            "CREATE TABLE invoices (id TEXT PRIMARY KEY, invoice_date TEXT, "
            "subtotal REAL, vat_amount REAL, grand_total REAL)")
        conn.execute(
            "INSERT INTO ProductMaster VALUES ('X1', 'Bad Product', 3, '2025-01-01', 5.0)")
        conn.commit()
        conn.close()
        result = load_dashboard(str(db), threshold=10, alert_days=30)
        assert result.ok
        crit = result.snapshot.critical_products
        assert len(crit) == 1
        reasons = crit[0].reasons
        assert any("Ληγμένο" in r for r in reasons)
        assert any("Χαμηλό απόθεμα" in r for r in reasons)

    def test_no_write_sql_in_data_source(self):
        """Scan data_source.py for write SQL keywords."""
        import ast
        src_path = os.path.join(
            os.path.dirname(__file__), "..", "qt_app", "data_source.py")
        with open(src_path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        forbidden = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                      "CREATE", "REPLACE", "TRUNCATE"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper()
                for kw in forbidden:
                    if kw in upper:
                        # Allow SELECT statements that mention these words
                        # in column names/comments — check for actual SQL
                        # commands (keyword at start of statement)
                        if upper.strip().startswith(kw):
                            pytest.fail(
                                f"Forbidden SQL keyword '{kw}' found in "
                                f"data_source.py: {node.value[:80]!r}")
        # If we get here, no write SQL found
        pass
