"""Tests for stock movements data source — pure Python, no Qt."""

import sqlite3, pytest
from qt_app.data_source import load_stock_movements, StockMovementsResult


def _make_current(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, change_amount INTEGER NOT NULL,
            reason TEXT NOT NULL, source TEXT, operator TEXT DEFAULT 'Σύστημα'
        );
        INSERT INTO stock_movements (timestamp, barcode, product_name, old_stock, new_stock, change_amount, reason, source, operator)
        VALUES ('2026-06-01 10:00', 'A', 'Ασπιρίνη', 0, 50, 50, 'Εισαγωγή', 'Qt Αποθήκη', 'admin');
        INSERT INTO stock_movements (timestamp, barcode, product_name, old_stock, new_stock, change_amount, reason, source, operator)
        VALUES ('2026-06-15 14:30', 'B', 'Depon 500mg', 20, 5, -15, 'Πώληση', 'POS', 'operator');
    """)
    conn.commit()
    conn.close()


def _make_legacy(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, difference INTEGER NOT NULL,
            reason TEXT NOT NULL, reference_id TEXT
        );
        INSERT INTO stock_movements (timestamp, barcode, product_name, old_stock, new_stock, difference, reason, reference_id)
        VALUES ('2026-05-01', 'L1', 'Legacy Product', 0, 100, 100, 'Εισαγωγή', 'old-system');
    """)
    conn.commit()
    conn.close()


class TestCurrentSchema:

    def test_load(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db)
        assert r.ok
        assert r.total == 2

    def test_change_values(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db)
        assert r.items[0].change_amount == -15  # newest first
        assert r.items[1].change_amount == 50

    def test_source_and_operator(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db)
        assert r.items[1].source == "Qt Αποθήκη"
        assert r.items[1].operator == "admin"

    def test_greek_accent_search(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, search_text="ασπιρινη")
        assert r.ok
        assert r.total == 1
        assert r.items[0].barcode == "A"

    def test_reason_filter(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, reason_filter="Πώληση")
        assert r.ok
        assert r.total == 1

    def test_date_filter(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, date_from="2026-06-10", date_to="2026-06-20")
        assert r.ok
        assert r.total == 1  # only Depon on June 15

    def test_pagination_clamp(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, page=999)
        assert r.ok
        assert r.page == 1

    def test_zero_results(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, search_text="NONEXISTENT")
        assert r.ok
        assert r.total == 0

    def test_reasons_list(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db)
        assert len(r.reasons) == 2
        assert "Εισαγωγή" in r.reasons


class TestLegacySchema:

    def test_load(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_legacy(db)
        r = load_stock_movements(db)
        assert r.ok
        assert r.total == 1

    def test_normalized_change(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_legacy(db)
        r = load_stock_movements(db)
        assert r.items[0].change_amount == 100
        assert r.items[0].source == "old-system"

    def test_no_source_operator(self, tmp_path):
        """Only difference + reference_id columns — source/operator are —."""
        db = str(tmp_path / "t.db")
        _make_legacy(db)
        r = load_stock_movements(db)
        assert r.items[0].source == "old-system"  # from reference_id
        assert r.items[0].operator == "—"


class TestErrors:

    def test_missing_table(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.close()
        r = load_stock_movements(db)
        assert not r.ok
        assert "κινήσεων" in r.error_message

    def test_missing_amount_columns(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE stock_movements (
                id INTEGER PRIMARY KEY, timestamp TEXT, barcode TEXT,
                product_name TEXT, old_stock INTEGER, new_stock INTEGER,
                reason TEXT
            );
        """)
        conn.commit()
        conn.close()
        r = load_stock_movements(db)
        assert not r.ok
        assert "μεταβολής" in r.error_message


class TestNoWrite:

    def test_no_write_sql(self):
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_stock_movements)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in stock movements source"


class TestDateValidation:

    def test_invalid_date_from(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, date_from="not-a-date")
        assert not r.ok
        assert "ημερομηνία" in r.error_message

    def test_invalid_date_to(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, date_to="2026-13-01")
        assert not r.ok
        assert "ημερομηνία" in r.error_message

    def test_date_from_after_date_to(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_current(db)
        r = load_stock_movements(db, date_from="2026-12-31", date_to="2026-01-01")
        assert not r.ok
        assert "από" in r.error_message

    def test_midnight_boundary_included(self, tmp_path):
        """A movement at 2026-06-15 23:59:59 is included when date_to=2026-06-15."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
                product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
                new_stock INTEGER NOT NULL, change_amount INTEGER NOT NULL,
                reason TEXT NOT NULL, source TEXT, operator TEXT
            );
            INSERT INTO stock_movements VALUES
                (1, '2026-06-15 23:59:59', 'A', 'Test', 0, 10, 10, 'Move', 'src', 'op');
        """)
        conn.commit()
        conn.close()
        r = load_stock_movements(db, date_to="2026-06-15")
        assert r.ok
        assert r.total == 1


class TestRequiredColumns:

    def test_missing_reason_column(self, tmp_path):
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE stock_movements (
                id INTEGER PRIMARY KEY, timestamp TEXT, barcode TEXT,
                product_name TEXT, old_stock INTEGER, new_stock INTEGER,
                change_amount INTEGER, source TEXT
            );
        """)
        conn.commit()
        conn.close()
        r = load_stock_movements(db)
        assert not r.ok
        assert "reason" in r.error_message
