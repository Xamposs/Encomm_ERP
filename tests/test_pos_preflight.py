"""Tests for POS preflight — pure Python, no Qt."""

import sqlite3, pytest
from datetime import date, timedelta
from qt_app.data_source import preflight_pos_sale, POSPreflightResult


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL
        );
        INSERT INTO ProductMaster VALUES ('A','Item A',10,'2027-12-31',5.0);
        INSERT INTO ProductMaster VALUES ('B','Item B',1,'2027-12-31',3.0);
        INSERT INTO ProductMaster VALUES ('C','Expired',5,'2020-01-01',2.0);
        INSERT INTO ProductMaster VALUES ('D','No Expiry',5,'',4.0);
        INSERT INTO ProductMaster VALUES ('E','Bad Expiry',5,'not-a-date',2.0);
    """)
    conn.commit()
    conn.close()


class TestPreflight:

    def test_valid_cart(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("A", 2), ("B", 1)])
        assert r.ok
        assert r.gross_total == 13.0
        assert len(r.lines) == 2
        assert all(l.valid for l in r.lines)

    def test_duplicate_aggregates(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("A", 2), ("A", 3)])
        assert r.ok
        assert len(r.lines) == 1
        assert r.lines[0].requested_qty == 5
        assert r.gross_total == 25.0

    def test_empty_cart(self, tmp_path):
        r = preflight_pos_sale(":memory:", [])
        assert not r.ok
        assert "άδειο" in r.error_message

    def test_invalid_qty_zero(self, tmp_path):
        r = preflight_pos_sale(":memory:", [("A", 0)])
        assert not r.ok

    def test_invalid_qty_bool(self, tmp_path):
        r = preflight_pos_sale(":memory:", [("A", True)])
        assert not r.ok

    def test_invalid_qty_negative(self, tmp_path):
        r = preflight_pos_sale(":memory:", [("A", -1)])
        assert not r.ok

    def test_empty_barcode(self, tmp_path):
        r = preflight_pos_sale(":memory:", [("", 1)])
        assert not r.ok
        assert "barcode" in r.error_message

    def test_missing_product(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("Z", 1)])
        assert not r.ok
        assert not r.lines[0].valid

    def test_insufficient_stock(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("B", 99)])
        assert not r.ok
        assert not r.lines[0].valid
        assert "Ανεπαρκές" in r.lines[0].error_message

    def test_expired_product(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("C", 1)])
        assert not r.ok
        assert "λήξει" in r.lines[0].error_message

    def test_blank_expiry_valid(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("D", 1)])
        assert r.ok

    def test_invalid_expiry_format(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_db(db)
        r = preflight_pos_sale(db, [("E", 1)])
        assert not r.ok
        assert "Μη έγκυρη ημερομηνία" in r.lines[0].error_message

    def test_missing_required_column(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE ProductMaster (Barcode TEXT, Name TEXT, Stock INT, Price REAL)")
        conn.close()
        r = preflight_pos_sale(db, [("A", 1)])
        assert not r.ok
        assert "ExpiryDate" in r.error_message

    def test_no_write_sql(self):
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds.preflight_pos_sale)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in preflight"

    def test_empty_generator(self):
        r = preflight_pos_sale(":memory:", (x for x in []))
        assert not r.ok
        assert "άδειο" in r.error_message

    def test_non_iterable(self):
        r = preflight_pos_sale(":memory:", 42)
        assert not r.ok
        assert "επαναλήψιμο" in r.error_message

    def test_malformed_line(self):
        r = preflight_pos_sale(":memory:", [("A",)])
        assert not r.ok
        assert "γραμμή" in r.error_message

    def test_malformed_stock_per_line(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE ProductMaster (Barcode TEXT PRIMARY KEY, Name TEXT,
                Stock INT, ExpiryDate TEXT, Price REAL);
            INSERT INTO ProductMaster VALUES ('A','OK',10,'2027-12-31',5.0);
            INSERT INTO ProductMaster VALUES ('B','Bad Stock','bad','2027-12-31',3.0);
        """)
        conn.commit()
        conn.close()
        r = preflight_pos_sale(db, [("A", 1), ("B", 1)])
        assert not r.ok
        ok_line = next(l for l in r.lines if l.barcode == "A")
        assert ok_line.valid
        bad_line = next(l for l in r.lines if l.barcode == "B")
        assert not bad_line.valid
        assert "απόθεμα" in bad_line.error_message
        assert bad_line.available_stock == 0

    def test_gross_total_rounding(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE ProductMaster (Barcode TEXT PRIMARY KEY, Name TEXT,
                Stock INT, ExpiryDate TEXT, Price REAL);
            INSERT INTO ProductMaster VALUES ('A','Item A',10,'2027-12-31',0.10);
            INSERT INTO ProductMaster VALUES ('B','Item B',10,'2027-12-31',0.20);
        """)
        conn.commit()
        conn.close()
        r = preflight_pos_sale(db, [("A", 1), ("B", 1)])
        assert r.ok
        assert r.gross_total == 0.30
