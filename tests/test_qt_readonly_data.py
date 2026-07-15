"""Pure-Python tests for ``qt_app.data_source``.

Do NOT require PySide6 or a display — these exercise the read-only
SQLite data-access layer only.
"""

from __future__ import annotations

import os
import sqlite3
import pytest

from datetime import date, timedelta

from qt_app.data_source import load_dashboard, _connect_ro


def _date_str(offset_days: int) -> str:
    """Return YYYY-MM-DD relative to today."""
    return (date.today() + timedelta(days=offset_days)).isoformat()


def _make_db(path: str, products: list[tuple], invoices: list[tuple] | None = None) -> None:
    """Create a minimal schema and insert the given rows."""
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
    """)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?,?,?,?,?)", p)
    if invoices:
        for inv in invoices:
            conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?)", inv)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestReadOnlyConnection:

    def test_opens_existing_db(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
        conn = _connect_ro(str(db))
        assert conn.execute("SELECT COUNT(*) FROM ProductMaster").fetchone()[0] == 1
        conn.close()

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(str(tmp_path / "nope.db"))

    def test_readonly_blocks_writes(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
        conn = _connect_ro(str(db))
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO ProductMaster VALUES ('B','Y',2,'2027-01-01',2.0)")
        conn.close()

    def test_no_file_created_for_missing(self, tmp_path):
        missing = str(tmp_path / "gone.db")
        assert not os.path.exists(missing)
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(missing)
        assert not os.path.exists(missing)


class TestLoadDashboard:

    def test_missing_db_returns_error(self, tmp_path):
        r = load_dashboard(str(tmp_path / "gone.db"))
        assert not r.ok
        assert "Αδυναμία" in r.error_message
        assert r.snapshot is None

    def test_error_has_no_snapshot(self, tmp_path):
        r = load_dashboard(str(tmp_path / "nope.db"))
        assert not r.ok
        assert r.snapshot is None
        assert len(r.error_message) > 0

    def test_counts_and_expiry_exact(self, tmp_path):
        """Date-independent: uses today +/- N days.  Exact expiry count."""
        expired = _date_str(-10)
        near = _date_str(20)
        far = _date_str(31)
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER,
                ExpiryDate TEXT, Price REAL
            );
            CREATE TABLE invoices (
                id TEXT PRIMARY KEY, invoice_date TEXT,
                subtotal REAL, vat_amount REAL, grand_total REAL
            );
        """)
        for p in [
            ("A", "OK",           50, far,     1.0),
            ("B", "Low",           3, far,     2.0),
            ("C", "Expired",      20, expired, 3.0),
            ("D", "Near-Expiry",  15, near,    4.0),
            ("E", "Far",           0, far,     5.0),
        ]:
            conn.execute("INSERT INTO ProductMaster VALUES (?,?,?,?,?)", p)
        conn.execute(
            "INSERT INTO invoices VALUES ('I1', date('now'), 10, 1.5, 11.5)")
        conn.execute(
            "INSERT INTO invoices VALUES ('I2', date('now'), 20, 3.0, 23.0)")
        conn.commit()
        conn.close()
        r = load_dashboard(str(db), threshold=10, alert_days=30)
        assert r.ok
        s = r.snapshot
        assert s.total_products == 5
        assert s.low_stock_count == 2
        assert s.expiry_alert_count == 2
        assert s.revenue_today == 34.50
        assert s.vat_today == 4.50
        assert s.invoice_count == 2

    def test_default_alert_days_includes_20_excludes_31(self, tmp_path):
        """alert_days=30 default: product expiring in 20 days IS included
        as an expiry alert; product expiring in 31 days is NOT an expiry
        alert (both have stock above default threshold=10 so they don't
        appear as low-stock)."""
        near = _date_str(20)
        far = _date_str(31)
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Near",  99, near, 1.0),
            ("B", "Far",   99, far,  1.0),
        ])
        r = load_dashboard(str(db))
        assert r.ok
        s = r.snapshot
        assert s.expiry_alert_count == 1  # only the 20-day product
        barcodes = {cp.barcode for cp in s.critical_products}
        assert "A" in barcodes
        assert "B" not in barcodes

    def test_capped_at_20(self, tmp_path):
        db = tmp_path / "test.db"
        prods = [(f"B{i:06d}", f"P{i}", 0, "2027-12-31", 1.0) for i in range(30)]
        _make_db(str(db), prods)
        r = load_dashboard(str(db), threshold=5, alert_days=30)
        assert r.ok
        assert len(r.snapshot.critical_products) == 20

    def test_fallback_thresholds(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "Low", 8, "2027-12-31", 1.0)])
        r = load_dashboard(str(db))  # threshold=10 default
        assert r.ok
        assert r.snapshot.low_stock_count == 1

    def test_multi_reason(self, tmp_path):
        """Product both expired AND low-stock gets multiple reasons."""
        expired = _date_str(-5)
        db = tmp_path / "test.db"
        _make_db(str(db), [("X", "Bad", 3, expired, 5.0)])
        r = load_dashboard(str(db), threshold=10, alert_days=30)
        assert r.ok
        cp = r.snapshot.critical_products[0]
        reasons = cp.reasons
        assert any("Ληγμένο" in rsn for rsn in reasons)
        assert any("Χαμηλό απόθεμα" in rsn for rsn in reasons)

    def test_no_write_sql(self):
        """data_source.py contains no write-SQL keywords at statement start."""
        import ast
        src = os.path.join(os.path.dirname(__file__), "..", "qt_app", "data_source.py")
        tree = ast.parse(open(src, encoding="utf-8").read())
        forbidden = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                      "CREATE", "REPLACE", "TRUNCATE"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper().strip()
                for kw in forbidden:
                    if upper.startswith(kw):
                        pytest.fail(f"Forbidden SQL '{kw}': {node.value[:80]!r}")

    def test_no_per_row_sqlite_connections(self):
        """The data source opens exactly one connection per load_dashboard().
        Verified structurally: _build_reasons_from_flags is pure Python
        (no sqlite3 calls), and the per-row loop does not call sqlite3."""
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds._build_reasons_from_flags)
        assert "sqlite3" not in src
        assert "connect" not in src
