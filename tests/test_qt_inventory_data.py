"""Pure-Python tests for inventory data source (``load_inventory_page``)."""

from __future__ import annotations

import os
import sqlite3
import pytest
from datetime import date, timedelta

from qt_app.data_source import load_inventory_page, InventoryProduct


def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _make_db(path: str, products: list[tuple], suppliers: list[str] | None = None) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode    TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock      INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE suppliers (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    """)
    if suppliers:
        for s in suppliers:
            conn.execute("INSERT INTO suppliers (name) VALUES (?)", (s,))
    for p in products:
        conn.execute("INSERT INTO ProductMaster VALUES (?,?,?,?,?,?)", p)
    conn.commit()
    conn.close()


def _make_db_no_suppliers(path: str, products: list[tuple]) -> None:
    """Schema WITHOUT suppliers table and WITHOUT supplier_id column."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL
        )
    """)
    for p in products:
        conn.execute("INSERT INTO ProductMaster VALUES (?,?,?,?,?)", p)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════

class TestInventoryLoad:

    # ── Basic loads ─────────────────────────────────────────────────

    def test_ok_loads_all(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Alpha",   5, "2027-01-01", 1.0, None),
            ("B", "Beta",   10, "2027-06-01", 2.0, None),
        ])
        r = load_inventory_page(str(db))
        assert r.ok
        assert r.snapshot.total_matching == 2
        assert len(r.snapshot.products) == 2

    def test_missing_db_error(self, tmp_path):
        r = load_inventory_page(str(tmp_path / "gone.db"))
        assert not r.ok
        assert "Αδυναμία" in r.error_message
        assert r.snapshot is None
        assert not os.path.exists(str(tmp_path / "gone.db"))

    # ── Unicode casefold search ─────────────────────────────────────

    def test_search_case_insensitive_latin(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Paracetamol", 5, "2027-01-01", 1.0, None),
            ("B", "Ibuprofen",  10, "2027-06-01", 2.0, None),
        ])
        assert load_inventory_page(str(db), search_text="par").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="PAR").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="Par").snapshot.total_matching == 1

    def test_search_greek_casefold(self, tmp_path):
        """Greek accent-insensitive + case-insensitive search."""
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Παρακεταμόλη",  5, "2027-01-01", 1.0, None),
            ("B", "Ασπιρίνη",     10, "2027-06-01", 2.0, None),
        ])
        # All these searches must find the matching product
        assert load_inventory_page(str(db), search_text="παρα").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="ΠΑΡΑ").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="ασπιρίνη").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="ΑΣΠΙΡΙΝΗ").snapshot.total_matching == 1
        assert load_inventory_page(str(db), search_text="ασπιρινη").snapshot.total_matching == 1

    def test_search_by_name(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Paracetamol",   5, "2027-01-01", 1.0, None),
            ("B", "Ibuprofen",    10, "2027-06-01", 2.0, None),
            ("C", "Paroxetine",   15, "2027-12-01", 3.0, None),
        ])
        r = load_inventory_page(str(db), search_text="par")
        assert r.ok
        assert r.snapshot.total_matching == 2

    def test_search_by_barcode(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("5200000000001", "Alpha", 5, "2027-01-01", 1.0, None),
            ("5200000000002", "Beta", 10, "2027-06-01", 2.0, None),
        ])
        r = load_inventory_page(str(db), search_text="000001")
        assert r.ok
        assert r.snapshot.total_matching == 1

    def test_like_wildcards_escaped(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "50% off",       5, "2027-01-01", 1.0, None),
            ("B", "Beta_special", 10, "2027-06-01", 2.0, None),
            ("C", "Normal",       15, "2027-12-01", 3.0, None),
        ])
        r = load_inventory_page(str(db), search_text="%")
        assert r.ok
        assert r.snapshot.total_matching == 1
        r2 = load_inventory_page(str(db), search_text="_")
        assert r2.ok
        assert r2.snapshot.total_matching == 1

    # ── Filters ─────────────────────────────────────────────────────

    def test_filter_expired(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Old",     5, _d(-10), 1.0, None),
            ("B", "Current", 5, _d(10),  2.0, None),
        ])
        r = load_inventory_page(str(db), status_filter="expired")
        assert r.ok
        assert r.snapshot.total_matching == 1

    def test_filter_near_expiry(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Near", 5, _d(20),  1.0, None),
            ("B", "Far",  5, _d(40),  2.0, None),
        ])
        r = load_inventory_page(str(db), status_filter="near_expiry", alert_days=30)
        assert r.ok
        assert r.snapshot.total_matching == 1

    def test_filter_low_stock(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Low",   3, "2027-01-01", 1.0, None),
            ("B", "High", 50, "2027-01-01", 2.0, None),
        ])
        r = load_inventory_page(str(db), status_filter="low_stock", threshold=10)
        assert r.ok
        assert r.snapshot.total_matching == 1

    def test_filter_available(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Good",     50, _d(100), 1.0, None),
            ("B", "Expired",  50, _d(-5),  2.0, None),
            ("C", "LowStock",  3, _d(100), 3.0, None),
        ])
        r = load_inventory_page(str(db), status_filter="available", threshold=10, alert_days=30)
        assert r.ok
        assert r.snapshot.total_matching == 1

    def test_multi_status(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("X", "Bad", 3, _d(-5), 5.0, None)])
        r = load_inventory_page(str(db), threshold=10, alert_days=30)
        assert r.ok
        labels = r.snapshot.products[0].status_labels
        assert any("Ληγμένο" in s for s in labels)
        assert any("Χαμηλό απόθεμα" in s for s in labels)

    # ── Pagination ──────────────────────────────────────────────────

    def test_pagination(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [(f"B{i:03d}", f"P{i}", 10, "2027-01-01", 1.0, None)
                            for i in range(25)])
        r = load_inventory_page(str(db), page=1, page_size=10)
        assert r.ok
        assert r.snapshot.total_matching == 25
        assert len(r.snapshot.products) == 10
        assert r.snapshot.page == 1

        r2 = load_inventory_page(str(db), page=3, page_size=10)
        assert r2.ok
        assert len(r2.snapshot.products) == 5
        assert r2.snapshot.page == 3

    def test_page_size_max_clamp(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [(f"B{i:03d}", f"P{i}", 10, "2027-01-01", 1.0, None)
                            for i in range(30)])
        r = load_inventory_page(str(db), page_size=200)
        assert r.ok
        assert r.snapshot.page_size <= 100

    def test_page_beyond_range_clamped_to_last(self, tmp_path):
        """Requesting page 999 returns the last valid page."""
        db = tmp_path / "test.db"
        _make_db(str(db), [(f"B{i:03d}", f"P{i}", 10, "2027-01-01", 1.0, None)
                            for i in range(25)])
        r = load_inventory_page(str(db), page=999, page_size=10)
        assert r.ok
        assert r.snapshot.page == 3  # pages 1-2 (10 each) + page 3 (5)

    def test_zero_results_returns_page_1(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [(f"B{i:03d}", f"P{i}", 10, "2027-01-01", 1.0, None)
                            for i in range(5)])
        r = load_inventory_page(str(db), search_text="NONEXISTENT")
        assert r.ok
        assert r.snapshot.total_matching == 0
        assert r.snapshot.page == 1
        assert len(r.snapshot.products) == 0

    # ── Suppliers ───────────────────────────────────────────────────

    def test_supplier_join(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "With Sup", 5, "2027-01-01", 1.0, 1)],
                 suppliers=["PharmaCorp"])
        r = load_inventory_page(str(db))
        assert r.ok
        assert r.snapshot.products[0].supplier_name == "PharmaCorp"
        assert r.snapshot.products[0].supplier_id == 1

    def test_null_supplier(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "No Sup", 5, "2027-01-01", 1.0, None)])
        r = load_inventory_page(str(db))
        assert r.ok
        assert r.snapshot.products[0].supplier_name == "—"
        assert r.snapshot.products[0].supplier_id is None

    def test_no_suppliers_table(self, tmp_path):
        """Inventory loads even when suppliers table doesn't exist."""
        db = tmp_path / "test.db"
        _make_db_no_suppliers(str(db), [("A", "NoSupTbl", 5, "2027-01-01", 1.0)])
        r = load_inventory_page(str(db))
        assert r.ok
        assert r.snapshot.products[0].supplier_name == "—"
        assert r.snapshot.products[0].supplier_id is None

    def test_no_supplier_id_column(self, tmp_path):
        """Inventory loads when ProductMaster has no supplier_id column."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER,
                ExpiryDate TEXT, Price REAL
            )
        """)
        conn.execute("INSERT INTO ProductMaster VALUES ('A','NoCol',5,'2027-01-01',1.0)")
        # Create suppliers table to prove we detect missing *column*, not table
        conn.execute("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()
        r = load_inventory_page(str(db))
        assert r.ok
        assert r.snapshot.products[0].supplier_name == "—"
        assert r.snapshot.products[0].supplier_id is None

    def test_no_write_sql(self):
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
