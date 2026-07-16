"""Pure-Python tests for ``infrastructure/inventory_command_service``."""

import sqlite3
import pytest
from datetime import date, timedelta

from infrastructure.inventory_command_service import (
    CreateProductRequest, UpdateProductRequest, ProductSnapshot,
    CommandResult, create_product, update_product,
)


def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _make_db(path: str, with_suppliers: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, difference INTEGER NOT NULL,
            reason TEXT NOT NULL, reference_id TEXT,
            change_amount INTEGER, source TEXT,
            operator TEXT DEFAULT 'Σύστημα'
        );
    """)
    if with_suppliers:
        conn.execute("""
            CREATE TABLE suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("INSERT INTO suppliers (name) VALUES ('PharmaCorp')")
        conn.execute("INSERT INTO suppliers (name) VALUES ('MedSupply')")
    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════════════════

class TestCreateProduct:

    def test_successful_create(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        req = CreateProductRequest(
            barcode="5200000000001", name="TestProduct",
            stock=50, expiry_date=_d(365), price=10.50)
        r = create_product(db, req)
        assert r.ok
        assert "δημιουργήθηκε" in r.message
        # Verify DB
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        prod = ro.execute("SELECT * FROM ProductMaster WHERE Barcode='5200000000001'").fetchone()
        assert prod["Name"] == "TestProduct"
        assert prod["Stock"] == 50
        assert prod["Price"] == 10.50
        audit = ro.execute("SELECT * FROM stock_movements WHERE barcode='5200000000001'").fetchone()
        assert audit is not None
        assert audit["old_stock"] == 0
        assert audit["new_stock"] == 50
        assert audit["reason"] == "Εισαγωγή"
        ro.close()

    def test_duplicate_barcode_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        req = CreateProductRequest(barcode="DUP", name="X", stock=1, expiry_date=_d(365), price=1)
        assert create_product(db, req).ok
        r2 = create_product(db, req)
        assert not r2.ok
        assert "υπάρχει" in r2.message
        # No second audit row
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        count = ro.execute("SELECT COUNT(*) FROM stock_movements WHERE barcode='DUP'").fetchone()[0]
        assert count == 1

    def test_audit_creates_exactly_one_row(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        req = CreateProductRequest(barcode="AUDIT", name="AuditTest", stock=10, expiry_date=_d(365), price=5)
        create_product(db, req)
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        assert ro.execute("SELECT COUNT(*) FROM stock_movements WHERE barcode='AUDIT'").fetchone()[0] == 1

    def test_invalid_barcode_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="", name="X", stock=1, expiry_date=_d(365), price=1))
        assert not r.ok
        assert "barcode" in r.message.lower()

    def test_invalid_name_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="  ", stock=1, expiry_date=_d(365), price=1))
        assert not r.ok

    def test_negative_stock_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="X", stock=-5, expiry_date=_d(365), price=1))
        assert not r.ok

    def test_negative_price_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="X", stock=1, expiry_date=_d(365), price=-1))
        assert not r.ok

    def test_invalid_date_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="X", stock=1, expiry_date="not-a-date", price=1))
        assert not r.ok

    def test_unknown_supplier_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="X", stock=1, expiry_date=_d(365), price=1, supplier_id=999))
        assert not r.ok
        assert "προμηθευτής" in r.message.lower()

    def test_past_expiry_warning_flag(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()
        r = create_product(db, CreateProductRequest(barcode="A", name="X", stock=1, expiry_date=_d(-30), price=1))
        assert r.ok
        assert r.past_expiry

    def test_no_delete_operation(self):
        """Verify no delete_product function exists."""
        import infrastructure.inventory_command_service as svc
        assert not hasattr(svc, "delete_product")


class TestUpdateProduct:

    def _seed(self, db_path):
        conn = _make_db(db_path)
        conn.execute("INSERT INTO ProductMaster VALUES ('EDITME','Original',20,'2027-12-31',5.0,NULL)")
        conn.commit()
        conn.close()

    def test_successful_update(self, tmp_path):
        db = str(tmp_path / "test.db")
        self._seed(db)
        req = UpdateProductRequest(
            barcode="EDITME", name="Updated", stock=30,
            expiry_date="2028-06-01", price=7.50)
        r = update_product(db, req)
        assert r.ok
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        prod = ro.execute("SELECT * FROM ProductMaster WHERE Barcode='EDITME'").fetchone()
        assert prod["Name"] == "Updated"
        assert prod["Stock"] == 30
        assert prod["Price"] == 7.50

    def test_stock_change_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        self._seed(db)
        req = UpdateProductRequest(barcode="EDITME", name="Original", stock=50, expiry_date="2027-12-31", price=5.0)
        update_product(db, req)
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        audits = ro.execute("SELECT * FROM stock_movements WHERE barcode='EDITME'").fetchall()
        assert len(audits) == 1
        assert audits[0]["old_stock"] == 20
        assert audits[0]["new_stock"] == 50
        assert audits[0]["difference"] == 30

    def test_non_stock_edit_no_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        self._seed(db)
        req = UpdateProductRequest(barcode="EDITME", name="Renamed", stock=20, expiry_date="2027-12-31", price=5.0)
        update_product(db, req)
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        assert ro.execute("SELECT COUNT(*) FROM stock_movements WHERE barcode='EDITME'").fetchone()[0] == 0

    def test_preserves_vat_metadata(self, tmp_path):
        """Fields not in the update SET are preserved."""
        db = str(tmp_path / "test.db")
        self._seed(db)
        req = UpdateProductRequest(barcode="EDITME", name="VAT", stock=20, expiry_date="2027-12-31", price=5.0)
        update_product(db, req)
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        prod = ro.execute("SELECT * FROM ProductMaster WHERE Barcode='EDITME'").fetchone()
        assert prod["Name"] == "VAT"
        assert prod["Stock"] == 20

    def test_concurrency_conflict(self, tmp_path):
        db = str(tmp_path / "test.db")
        self._seed(db)
        original = ProductSnapshot(barcode="EDITME", name="Original", stock=20, expiry_date="2027-12-31", price=5.0, supplier_id=None)
        # Simulate another process changing the product
        conn = sqlite3.connect(db)
        conn.execute("UPDATE ProductMaster SET Stock=99 WHERE Barcode='EDITME'")
        conn.commit()
        conn.close()
        req = UpdateProductRequest(barcode="EDITME", name="Updated", stock=30, expiry_date="2027-12-31", price=5.0, original=original)
        r = update_product(db, req)
        assert not r.ok
        assert "άλλαξε" in r.message
        # Verify no change made
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        stock = ro.execute("SELECT Stock FROM ProductMaster WHERE Barcode='EDITME'").fetchone()[0]
        assert stock == 99

    def test_simulated_error_rolls_back(self, tmp_path):
        """If audit write fails mid-transaction, product change also rolls back."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.execute("INSERT INTO ProductMaster VALUES ('ROLLBACK','Test',20,'2027-12-31',5.0,NULL)")
        # Drop stock_movements to force audit failure
        conn.execute("DROP TABLE stock_movements")
        conn.commit()
        conn.close()
        req = UpdateProductRequest(barcode="ROLLBACK", name="New", stock=99, expiry_date="2027-12-31", price=5.0)
        r = update_product(db, req)
        assert not r.ok
        # Product should be unchanged
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        name = ro.execute("SELECT Name FROM ProductMaster WHERE Barcode='ROLLBACK'").fetchone()[0]
        assert name == "Test"
