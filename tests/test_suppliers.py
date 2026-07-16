"""Tests for supplier data source — pure Python, no Qt."""

import sqlite3, pytest
from qt_app.data_source import (
    load_suppliers_page, load_supplier_detail,
    SupplierPageResult, SupplierDetailResult,
)


def _make_db(path, with_suppliers=True, with_product=False):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER,
            ExpiryDate TEXT, Price REAL, supplier_id INTEGER
        )
    """)
    if with_suppliers:
        conn.executescript("""
            CREATE TABLE suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, phone TEXT, email TEXT, address TEXT
            );
            INSERT INTO suppliers (name, phone, email) VALUES
                ('Φάρμακο ΑΕ', '2105551000', 'info@farma.gr');
            INSERT INTO suppliers (name, phone, email) VALUES
                ('MediCorp', '2310555200', 'sales@medi.gr');
        """)
    if with_product:
        conn.execute("INSERT INTO ProductMaster VALUES ('A','Test',10,'2027-01-01',5.0,1)")
    conn.commit()
    conn.close()


class TestSuppliers:

    def test_load_page(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_suppliers_page(db)
        assert r.ok
        assert r.total == 2
        assert len(r.items) == 2

    def test_greek_search_accent_insensitive(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r1 = load_suppliers_page(db, search_text="φαρμα")
        assert r1.ok
        assert r1.total == 1
        r2 = load_suppliers_page(db, search_text="ΦΑΡΜΑ")
        assert r2.ok
        assert r2.total == 1

    def test_pagination_bounds(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_suppliers_page(db, page=999)
        assert r.ok
        assert r.page == 1  # clamped to last page (1 page total)

    def test_zero_results_page_1(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_suppliers_page(db, search_text="NONEXISTENT")
        assert r.ok
        assert r.total == 0
        assert r.page == 1
        assert len(r.items) == 0

    def test_product_count(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db, with_product=True)
        r = load_suppliers_page(db)
        assert r.ok
        # Alphabetically: MediCorp (0 prods) then Φάρμακο ΑΕ (1 prod)
        names = {s.name: s.product_count for s in r.items}
        assert names["Φάρμακο ΑΕ"] == 1
        assert names["MediCorp"] == 0

    def test_no_suppliers_table(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db, with_suppliers=False)
        r = load_suppliers_page(db)
        assert not r.ok
        assert "προμηθευτών" in r.error_message

    def test_missing_optional_columns(self, tmp_path):
        """Schema with only base columns (no tax_id, etc) still works."""
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_suppliers_page(db)
        assert r.ok
        # tax_id should fall back to "—"
        assert r.items[0].tax_id == "—"

    def test_supplier_detail(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_supplier_detail(db, 1)
        assert r.ok
        assert r.supplier.name == "Φάρμακο ΑΕ"

    def test_detail_missing_id(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_supplier_detail(db, 999)
        assert not r.ok

    def test_no_write_sql(self):
        """load_suppliers_page and load_supplier_detail contain no DML/DDL."""
        import inspect, os
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_suppliers_page)
        src += inspect.getsource(ds.load_supplier_detail)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in supplier source"

    def test_minimal_schema_no_supplier_id(self, tmp_path):
        """Suppliers table with only id+name, ProductMaster without supplier_id."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO suppliers (name) VALUES ('MinimalCorp');
            CREATE TABLE ProductMaster (Barcode TEXT, Name TEXT, Stock INT,
                                        ExpiryDate TEXT, Price REAL);
            INSERT INTO ProductMaster VALUES ('A','X',1,'2027-01-01',1.0);
        """)
        conn.commit()
        conn.close()
        # List should succeed with product_count=0
        r = load_suppliers_page(db)
        assert r.ok, r.error_message
        assert r.items[0].product_count == 0
        # Detail should succeed with product_count=0
        rd = load_supplier_detail(db, 1)
        assert rd.ok, rd.error_message
        assert rd.supplier.product_count == 0

    def test_no_email_column_search_works(self, tmp_path):
        """Suppliers with no email column: name search works, email-like search returns empty OK."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO suppliers (name) VALUES ('TestSupplier');
        """)
        conn.commit()
        conn.close()
        r1 = load_suppliers_page(db, search_text="Test")
        assert r1.ok
        assert r1.total == 1
        r2 = load_suppliers_page(db, search_text="nonexistent@email.com")
        assert r2.ok
        assert r2.total == 0

    def test_default_markup_zero_displays_zero(self, tmp_path):
        """default_markup=0 should render as '0', not '—'."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT,
                                    default_markup REAL);
            INSERT INTO suppliers (name, default_markup) VALUES ('ZeroCo', 0);
        """)
        conn.commit()
        conn.close()
        rd = load_supplier_detail(db, 1)
        assert rd.ok
        assert rd.supplier.default_markup == "0"
