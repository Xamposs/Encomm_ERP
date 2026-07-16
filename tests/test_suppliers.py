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
        import ast, os
        src = os.path.join(os.path.dirname(__file__), "..", "qt_app", "data_source.py")
        tree = ast.parse(open(src, encoding="utf-8").read())
        forbidden = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "TRUNCATE"}
        in_supplier_section = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "Supplier data source" in node.value:
                    in_supplier_section = True
                if in_supplier_section:
                    upper = node.value.upper().strip()
                    for kw in forbidden:
                        if upper.startswith(kw):
                            pytest.fail(f"Forbidden '{kw}': {node.value[:80]}")
