"""Tests for POS catalog data source — pure Python, no Qt."""

import sqlite3, pytest
from datetime import date, timedelta
from qt_app.data_source import load_pos_catalog_page, POSCatalogResult


def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL
        );
        INSERT INTO ProductMaster VALUES ('A','Ασπιρίνη',50,'',5.0);
        INSERT INTO ProductMaster VALUES ('B','Depon 500mg',30,'2027-12-31',3.5);
        INSERT INTO ProductMaster VALUES ('C','Expired Item',5,'2020-01-01',2.0);
        INSERT INTO ProductMaster VALUES ('D','Zero Stock',0,'2027-12-31',1.0);
        INSERT INTO ProductMaster VALUES ('E','Percent%Item',10,'2027-12-31',1.0);
        INSERT INTO ProductMaster VALUES ('F','Back\\slash Item',10,'2027-12-31',2.0);
    """)
    conn.commit()
    conn.close()


class TestCatalog:

    def test_load(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db)
        assert r.ok
        # Only A, B, E, F are sellable (C expired, D zero stock)
        assert r.total == 4

    def test_greek_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="ασπιρινη")
        assert r.ok
        assert r.total == 1
        assert r.products[0].barcode == "A"

    def test_expired_excluded(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="Expired")
        assert r.ok
        assert r.total == 0  # expired, excluded

    def test_zero_stock_excluded(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="Zero")
        assert r.ok
        assert r.total == 0

    def test_blank_expiry_included(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="Ασπιρίνη")
        assert r.ok
        assert r.products[0].expiry_date == "—"

    def test_literal_percent_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="%")
        assert r.ok
        assert r.total == 1
        assert "Percent" in r.products[0].name

    def test_literal_underscore_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="_")
        assert r.ok
        assert r.total == 0  # no product has literal underscore

    def test_literal_backslash_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = load_pos_catalog_page(db, search_text="\\")
        assert r.ok
        assert r.total == 1
        assert "Back\\slash" in r.products[0].name

    def test_pagination_clamp(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        # 4 sellable products (A, B, E, F). page_size=2 → 2 pages.
        r1 = load_pos_catalog_page(db, page_size=2, page=1)
        assert r1.ok
        assert r1.page == 1
        assert len(r1.products) == 2
        r2 = load_pos_catalog_page(db, page_size=2, page=2)
        assert r2.ok
        assert r2.page == 2
        assert len(r2.products) == 2  # 4 total: A on pg1, F on pg2
        # Clamp beyond last page
        r3 = load_pos_catalog_page(db, page_size=2, page=999)
        assert r3.ok
        assert r3.page == 2

    def test_missing_required_column(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE ProductMaster (Barcode TEXT, Name TEXT, Stock INT, Price REAL);
        """)
        conn.commit()
        conn.close()
        r = load_pos_catalog_page(db)
        assert not r.ok
        assert "ExpiryDate" in r.error_message

    def test_no_write_sql(self):
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_pos_catalog_page)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in POS catalog source"
