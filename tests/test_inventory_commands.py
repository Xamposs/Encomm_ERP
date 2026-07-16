"""Pure-Python tests for inventory_command_service — two real schemas."""

import sqlite3
import pytest
from datetime import date, timedelta

from infrastructure.inventory_command_service import (
    CreateProductRequest, UpdateProductRequest, ProductSnapshot,
    create_product, update_product,
)


def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# Schema A — CURRENT (change_amount, source, supplier_id; NO difference)
# ═══════════════════════════════════════════════════════════════════════

def _make_current(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, reason TEXT NOT NULL,
            change_amount INTEGER, source TEXT,
            operator TEXT DEFAULT 'Σύστημα'
        );
    """)
    conn.execute("INSERT INTO suppliers (name) VALUES ('PharmaCorp')")
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Schema B — LEGACY (difference, reference_id; NO change_amount, source,
#            NO supplier_id in ProductMaster)
# ═══════════════════════════════════════════════════════════════════════

def _make_legacy(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, difference INTEGER NOT NULL,
            reason TEXT NOT NULL, reference_id TEXT
        );
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Tests — current schema
# ═══════════════════════════════════════════════════════════════════════

class TestCurrentSchema:

    def test_create_and_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        r = create_product(db, CreateProductRequest(
            barcode="5200000000001", name="Test", stock=50,
            expiry_date=_d(365), price=10.50))
        assert r.ok
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        audit = ro.execute(
            "SELECT * FROM stock_movements WHERE barcode='5200000000001'").fetchone()
        assert audit is not None
        assert audit["old_stock"] == 0
        assert audit["new_stock"] == 50
        assert audit["change_amount"] == 50
        assert audit["source"] == "Qt Αποθήκη"
        # No difference column should exist
        cols = [r[1] for r in ro.execute("PRAGMA table_info('stock_movements')")]
        assert "difference" not in cols

    def test_update_stock_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('EDIT','Orig',20,'2027-12-31',5.0,1)")
        conn.commit()
        conn.close()
        orig = ProductSnapshot("EDIT", "Orig", 20, "2027-12-31", 5.0, 1)
        r = update_product(db, UpdateProductRequest(
            barcode="EDIT", name="Orig", stock=50, expiry_date="2027-12-31",
            price=5.0, supplier_id=1, original=orig))
        assert r.ok
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        audit = ro.execute(
            "SELECT * FROM stock_movements WHERE barcode='EDIT'").fetchone()
        assert audit["change_amount"] == 30
        assert audit["old_stock"] == 20

    def test_rollback_on_audit_failure(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE ProductMaster (Barcode TEXT PRIMARY KEY, Name TEXT, Stock INT, ExpiryDate TEXT, Price REAL, supplier_id INT);
        """)
        conn.execute("INSERT INTO ProductMaster VALUES ('RB','X',20,'2027-12-31',5.0,NULL)")
        # No stock_movements table → audit will fail
        conn.commit()
        conn.close()
        orig = ProductSnapshot("RB", "X", 20, "2027-12-31", 5.0, None)
        r = update_product(db, UpdateProductRequest(
            barcode="RB", name="X", stock=99, expiry_date="2027-12-31",
            price=5.0, original=orig))
        assert not r.ok
        # Product must be unchanged
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        assert ro.execute("SELECT Stock FROM ProductMaster WHERE Barcode='RB'").fetchone()[0] == 20

    def test_supplier_linked_edit_no_false_conflict(self, tmp_path):
        """Edit product with supplier_id=1 — original snapshot must match."""
        db = str(tmp_path / "test.db")
        _make_current(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('S','WithSup',10,'2027-12-31',5.0,1)")
        conn.commit()
        conn.close()
        orig = ProductSnapshot("S", "WithSup", 10, "2027-12-31", 5.0, 1)
        r = update_product(db, UpdateProductRequest(
            barcode="S", name="WithSup", stock=10, expiry_date="2027-12-31",
            price=6.0, supplier_id=1, original=orig))
        assert r.ok

    def test_invalid_price_inputs(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        for bad in [None, "abc", float("nan"), float("inf"), True, False]:
            r = create_product(db, CreateProductRequest(
                barcode="BAD", name="X", stock=1, expiry_date=_d(365), price=bad))
            assert not r.ok, f"Should reject price={bad!r}"

    def test_bool_stock_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        r = create_product(db, CreateProductRequest(
            barcode="B", name="X", stock=True, expiry_date=_d(365), price=1.0))
        assert not r.ok


# ═══════════════════════════════════════════════════════════════════════
# Tests — legacy schema
# ═══════════════════════════════════════════════════════════════════════

class TestLegacySchema:

    def test_create_and_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy(db)
        r = create_product(db, CreateProductRequest(
            barcode="L001", name="Legacy", stock=30, expiry_date=_d(365), price=5.0))
        assert r.ok
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        audit = ro.execute(
            "SELECT * FROM stock_movements WHERE barcode='L001'").fetchone()
        assert audit["difference"] == 30
        assert audit["reference_id"] == "Qt Αποθήκη"
        cols = [r[1] for r in ro.execute("PRAGMA table_info('stock_movements')")]
        assert "change_amount" not in cols
        assert "source" not in cols

    def test_update_and_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('LE','Old',10,'2027-12-31',5.0)")
        conn.commit()
        conn.close()
        orig = ProductSnapshot("LE", "Old", 10, "2027-12-31", 5.0, None)
        r = update_product(db, UpdateProductRequest(
            barcode="LE", name="Old", stock=25, expiry_date="2027-12-31",
            price=5.0, original=orig))
        assert r.ok
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        audit = ro.execute(
            "SELECT * FROM stock_movements WHERE barcode='LE'").fetchone()
        assert audit["difference"] == 15
        assert audit["old_stock"] == 10

    def test_no_supplier_id_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy(db)
        r = create_product(db, CreateProductRequest(
            barcode="NS", name="NoSup", stock=1, expiry_date=_d(365),
            price=1.0, supplier_id=1))
        assert not r.ok
        assert "προμηθευτών" in r.message

    def test_legacy_update_no_supplier_id(self, tmp_path):
        """Update on legacy schema (no supplier_id col) works without it."""
        db = str(tmp_path / "test.db")
        _make_legacy(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('L2','Orig',5,'2027-12-31',5.0)")
        conn.commit()
        conn.close()
        orig = ProductSnapshot("L2", "Orig", 5, "2027-12-31", 5.0, None)
        r = update_product(db, UpdateProductRequest(
            barcode="L2", name="Updated", stock=5, expiry_date="2027-12-31",
            price=6.0, original=orig))
        assert r.ok


# ═══════════════════════════════════════════════════════════════════════
# Cross-cutting contract tests
# ═══════════════════════════════════════════════════════════════════════

class TestConcurrencyContract:

    def test_missing_original_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        r = update_product(db, UpdateProductRequest(
            barcode="X", name="X", stock=1, expiry_date=_d(365), price=1.0))
        assert not r.ok
        assert "στιγμιότυπο" in r.message

    def test_mismatched_barcode_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        orig = ProductSnapshot("OTHER", "X", 1, _d(365), 1.0, None)
        r = update_product(db, UpdateProductRequest(
            barcode="WRONG", name="X", stock=1, expiry_date=_d(365),
            price=1.0, original=orig))
        assert not r.ok
        assert "barcode" in r.message.lower() or "ταιριάζει" in r.message

    def test_concurrency_conflict(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('CC','Orig',20,'2027-12-31',5.0,NULL)")
        conn.commit()
        conn.close()
        orig = ProductSnapshot("CC", "Orig", 20, "2027-12-31", 5.0, None)
        # Another process changes stock
        conn2 = sqlite3.connect(db)
        conn2.execute("UPDATE ProductMaster SET Stock=99 WHERE Barcode='CC'")
        conn2.commit()
        conn2.close()
        r = update_product(db, UpdateProductRequest(
            barcode="CC", name="Orig", stock=30, expiry_date="2027-12-31",
            price=5.0, original=orig))
        assert not r.ok
        assert "άλλαξε" in r.message
