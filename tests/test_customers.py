"""Tests for customer data source — pure Python, no Qt."""

import sqlite3, pytest
from qt_app.data_source import (
    load_customers_page, load_customer_detail,
    CustomerPageResult, CustomerDetailResult,
)


def _make_db(path, with_customers=True, with_invoices=False):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    if with_customers:
        conn.executescript("""
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, amka TEXT UNIQUE, phone TEXT
            );
            INSERT INTO customers (name, amka, phone) VALUES
                ('Γεώργιος Παπαδόπουλος', '01018000001', '6970000001');
            INSERT INTO customers (name, amka, phone) VALUES
                ('Μαρία Ιωάννου', '05059000002', '6970000002');
        """)
    if with_invoices:
        conn.executescript("""
            CREATE TABLE invoices (
                id TEXT PRIMARY KEY, invoice_date TEXT,
                subtotal REAL, vat_amount REAL, grand_total REAL,
                customer_id INTEGER
            );
            INSERT INTO invoices VALUES
                ('INV1','2026-01-15',10,1.5,11.5,1);
            INSERT INTO invoices VALUES
                ('INV2','2026-03-20',20,3,23,1);
            INSERT INTO invoices VALUES
                ('INV3','2026-06-01',5,0.75,5.75,2);
        """)
    conn.commit()
    conn.close()


class TestCustomers:

    def test_load_page(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customers_page(db)
        assert r.ok
        assert r.total == 2
        assert len(r.items) == 2

    def test_greek_accent_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r1 = load_customers_page(db, search_text="γεωργιο")
        assert r1.ok
        assert r1.total == 1
        r2 = load_customers_page(db, search_text="ΓΕΩΡΓΙΟ")
        assert r2.ok
        assert r2.total == 1

    def test_amka_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customers_page(db, search_text="050590")
        assert r.ok
        assert r.total == 1
        assert "Μαρία" in r.items[0].name

    def test_phone_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customers_page(db, search_text="6970000002")
        assert r.ok
        assert r.total == 1

    def test_invoice_count_and_total(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db, with_invoices=True)
        r = load_customers_page(db)
        assert r.ok
        names = {c.name: (c.invoice_count, c.total_purchases) for c in r.items}
        assert names["Γεώργιος Παπαδόπουλος"] == (2, 34.50)
        assert names["Μαρία Ιωάννου"] == (1, 5.75)

    def test_latest_invoice_date(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db, with_invoices=True)
        rd = load_customer_detail(db, 1)
        assert rd.ok
        assert rd.customer.latest_invoice_date == "2026-03-20"

    def test_legacy_no_invoices(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)  # no invoices table
        r = load_customers_page(db)
        assert r.ok
        assert r.items[0].invoice_count == 0
        assert r.items[0].total_purchases == 0.0

    def test_legacy_no_customer_id(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, amka TEXT, phone TEXT)")
        conn.execute("INSERT INTO customers VALUES (1,'X','A','P')")
        conn.execute("CREATE TABLE invoices (id TEXT, invoice_date TEXT, subtotal REAL, vat_amount REAL, grand_total REAL)")
        conn.execute("INSERT INTO invoices VALUES ('I1','2026-01-01',10,1.5,11.5)")
        conn.commit()
        conn.close()
        r = load_customers_page(db)
        assert r.ok
        assert r.items[0].invoice_count == 0

    def test_missing_amka_phone(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO customers (name) VALUES ('Minimal')")
        conn.commit()
        conn.close()
        r = load_customers_page(db)
        assert r.ok
        assert r.items[0].amka == "—"
        assert r.items[0].phone == "—"

    def test_missing_customers_table(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.close()
        r = load_customers_page(db)
        assert not r.ok
        assert "πελατών" in r.error_message

    def test_detail_missing_id(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customer_detail(db, 999)
        assert not r.ok

    def test_pagination_clamp(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customers_page(db, page=999)
        assert r.ok
        assert r.page == 1

    def test_zero_results(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_customers_page(db, search_text="NONEXISTENT")
        assert r.ok
        assert r.total == 0
        assert r.page == 1

    def test_no_write_sql(self):
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_customers_page)
        src += inspect.getsource(ds.load_customer_detail)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in customer source"
