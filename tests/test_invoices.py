"""Tests for invoice data source — pure Python, no Qt."""

import sqlite3, pytest
from qt_app.data_source import (
    load_invoices_page, load_invoice_detail,
    InvoicePageResult, InvoiceDetailResult,
)


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, amka TEXT, phone TEXT);
        INSERT INTO customers VALUES (1,'Γεώργιος Παπαδόπουλος','A1','P1');
        CREATE TABLE invoices (
            id TEXT PRIMARY KEY, invoice_date TEXT, subtotal REAL,
            vat_amount REAL, grand_total REAL, customer_id INTEGER
        );
        INSERT INTO invoices VALUES ('INV1','2026-06-01',100,15,115,1);
        INSERT INTO invoices VALUES ('INV2','2026-06-15',200,30,230,1);
        INSERT INTO invoices VALUES ('INV3','2026-07-01',50,7.5,57.5,NULL);
        CREATE TABLE invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT,
            barcode TEXT, name TEXT, quantity INTEGER, price REAL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        );
        INSERT INTO invoice_items (invoice_id,barcode,name,quantity,price)
        VALUES ('INV1','A','Product A',2,10.0);
        INSERT INTO invoice_items (invoice_id,barcode,name,quantity,price)
        VALUES ('INV1','B','Product B',1,30.0);
    """)
    conn.commit()
    conn.close()


class TestInvoiceList:

    def test_load(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db)
        assert r.ok
        assert r.total == 3

    def test_id_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, search_text="INV2")
        assert r.ok
        assert r.total == 1

    def test_customer_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, search_text="Γεώργιο")
        assert r.ok
        assert r.items[0].customer_name == "Γεώργιος Παπαδόπουλος"

    def test_date_filter(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, date_from="2026-06-01", date_to="2026-06-01")
        assert r.ok
        assert r.total == 1

    def test_pagination(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, page_size=1, page=2)
        assert r.ok
        assert r.page == 2

    def test_invalid_date(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, date_from="bad")
        assert not r.ok
        assert "ημερομηνία" in r.error_message

    def test_reversed_dates(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoices_page(db, date_from="2026-12-31", date_to="2026-01-01")
        assert not r.ok
        assert "από" in r.error_message

    def test_no_customer_linkage(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE invoices (id TEXT, invoice_date TEXT, subtotal REAL,
                                   vat_amount REAL, grand_total REAL);
            INSERT INTO invoices VALUES ('IV','2026-06-01',10,1.5,11.5);
        """)
        conn.commit()
        conn.close()
        r = load_invoices_page(db)
        assert r.ok
        assert r.items[0].customer_name == "—"


class TestInvoiceDetail:

    def test_detail(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoice_detail(db, "INV1")
        assert r.ok
        assert r.invoice.grand_total == 115
        assert len(r.invoice.items) == 2
        assert r.invoice.items[0].barcode == "A"
        assert r.invoice.items[0].line_total == 20.0

    def test_no_items_table(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE invoices (id TEXT, invoice_date TEXT, subtotal REAL,
                                   vat_amount REAL, grand_total REAL);
            INSERT INTO invoices VALUES ('X','2026-01-01',1,0.15,1.15);
        """)
        conn.commit()
        conn.close()
        r = load_invoice_detail(db, "X")
        assert r.ok
        assert len(r.invoice.items) == 0

    def test_not_found(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_invoice_detail(db, "NOPE")
        assert not r.ok

    def test_no_write_sql(self):
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_invoices_page)
        src += inspect.getsource(ds.load_invoice_detail)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}'"
