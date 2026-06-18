import sys
import os
import tempfile

# Dynamically append project root so we can import infrastructure modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from infrastructure.database_service import DatabaseService


@pytest.fixture
def db():
    """Provide a fresh temp-file database for each test — zero production impact.
    
    Uses a temporary file on disk (not :memory:) because DatabaseService opens
    a new SQLite connection per method call. SQLite :memory: databases are
    per-connection — a second connection sees an empty database — so :memory:
    is incompatible with this connection pattern. The temp file is cleaned up
    after each test, giving the same isolation guarantees.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    service = DatabaseService(path)
    yield service
    try:
        os.unlink(path)
    except OSError:
        pass


class TestDatabaseInitialization:
    """Schema, table, and index integrity checks."""

    def test_db_initialization_and_indices(self, db):
        conn = db._get_connection()
        cursor = conn.cursor()

        # Verify core tables exist
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {r["name"] for r in tables}

        assert "ProductMaster" in table_names, "ProductMaster table missing"
        assert "suppliers" in table_names, "suppliers table missing"
        assert "invoices" in table_names, "invoices table missing"
        assert "customers" in table_names, "customers table missing"
        assert "SystemConfig" in table_names, "SystemConfig table missing"

        # Verify indexes exist
        indexes = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ).fetchall()
        index_names = {r["name"] for r in indexes}

        assert "idx_invoices_date" in index_names, "idx_invoices_date index missing"
        assert "idx_product_expiry" in index_names, "idx_product_expiry index missing"
        assert "idx_product_supplier" in index_names, "idx_product_supplier index missing"

        conn.close()


class TestSupplierCRUD:
    """Supplier add / read / delete lifecycle."""

    def test_add_and_get_suppliers(self, db):
        assert db.add_supplier("DemoSupplier A", "2105551234", "a@demo.gr", "Athens")
        assert db.add_supplier("DemoSupplier B", "2105555678", "b@demo.gr", "Thessaloniki")

        suppliers = db.get_all_suppliers()
        assert len(suppliers) == 2
        assert suppliers[0]["name"] == "DemoSupplier A"
        assert suppliers[0]["phone"] == "2105551234"
        assert suppliers[1]["email"] == "b@demo.gr"

        # Check column keys
        for s in suppliers:
            for key in ("id", "name", "phone", "email", "address"):
                assert key in s, f"Missing key '{key}' in supplier dict"

    def test_delete_supplier(self, db):
        db.add_supplier("ToDelete", "", "", "")
        suppliers = db.get_all_suppliers()
        sid = suppliers[0]["id"]

        assert db.delete_supplier(sid)
        assert len(db.get_all_suppliers()) == 0

    def test_duplicate_supplier_fails(self, db):
        assert db.add_supplier("UniqueSupplier")
        assert not db.add_supplier("UniqueSupplier")  # UNIQUE constraint


class TestDateFilteredQueries:
    """Invoice date-range filtering accuracy."""

    def _seed_invoices(self, db):
        conn = db._get_connection()
        conn.executescript("""
            INSERT INTO invoices (id, invoice_date, subtotal, vat_amount, grand_total)
            VALUES ('INV-001', '2026-06-01', 100.0, 15.0, 115.0);
            INSERT INTO invoices (id, invoice_date, subtotal, vat_amount, grand_total)
            VALUES ('INV-002', '2026-06-15', 200.0, 30.0, 230.0);
            INSERT INTO invoices (id, invoice_date, subtotal, vat_amount, grand_total)
            VALUES ('INV-003', '2026-07-01', 300.0, 45.0, 345.0);
        """)
        conn.commit()
        conn.close()

    def test_get_all_invoices_no_filter(self, db):
        self._seed_invoices(db)
        result = db.get_all_invoices()
        assert len(result) == 3

    def test_date_range_filter_inclusive(self, db):
        self._seed_invoices(db)
        result = db.get_all_invoices(start_date="2026-06-01", end_date="2026-06-15")
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"INV-001", "INV-002"}

    def test_date_filter_single_day(self, db):
        self._seed_invoices(db)
        result = db.get_all_invoices(start_date="2026-07-01", end_date="2026-07-01")
        assert len(result) == 1
        assert result[0]["id"] == "INV-003"

    def test_date_filter_no_results(self, db):
        self._seed_invoices(db)
        result = db.get_all_invoices(start_date="2025-01-01", end_date="2025-01-01")
        assert len(result) == 0
