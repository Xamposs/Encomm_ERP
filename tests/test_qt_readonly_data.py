"""Pure-Python tests for ``qt_app.data_source``.

Do NOT require PySide6 or a display — these exercise the read-only
SQLite data-access layer only.
"""

from __future__ import annotations

import os
import sqlite3
import pytest

from datetime import date, timedelta

from qt_app.data_source import load_dashboard, _connect_ro


def _date_str(offset_days: int) -> str:
    """Return YYYY-MM-DD relative to today."""
    return (date.today() + timedelta(days=offset_days)).isoformat()


def _make_db(path: str, products: list[tuple], invoices: list[tuple] | None = None) -> None:
    """Create a minimal schema and insert the given rows."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL,
            ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL
        );
        CREATE TABLE invoices (
            id           TEXT PRIMARY KEY,
            invoice_date TEXT    NOT NULL,
            subtotal     REAL    NOT NULL,
            vat_amount   REAL    NOT NULL,
            grand_total  REAL    NOT NULL
        );
    """)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?,?,?,?,?)", p)
    if invoices:
        for inv in invoices:
            conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?)", inv)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestReadOnlyConnection:

    def test_opens_existing_db(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
        conn = _connect_ro(str(db))
        assert conn.execute("SELECT COUNT(*) FROM ProductMaster").fetchone()[0] == 1
        conn.close()

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(str(tmp_path / "nope.db"))

    def test_readonly_blocks_writes(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
        conn = _connect_ro(str(db))
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO ProductMaster VALUES ('B','Y',2,'2027-01-01',2.0)")
        conn.close()

    def test_no_file_created_for_missing(self, tmp_path):
        missing = str(tmp_path / "gone.db")
        assert not os.path.exists(missing)
        with pytest.raises(sqlite3.OperationalError):
            _connect_ro(missing)
        assert not os.path.exists(missing)


class TestLoadDashboard:

    def test_missing_db_returns_error(self, tmp_path):
        r = load_dashboard(str(tmp_path / "gone.db"))
        assert not r.ok
        assert "Αδυναμία" in r.error_message
        assert r.snapshot is None

    def test_error_has_no_snapshot(self, tmp_path):
        r = load_dashboard(str(tmp_path / "nope.db"))
        assert not r.ok
        assert r.snapshot is None
        assert len(r.error_message) > 0

    def test_counts_and_expiry_exact(self, tmp_path):
        """Date-independent: uses today +/- N days.  Exact expiry count."""
        expired = _date_str(-10)
        near = _date_str(20)
        far = _date_str(31)
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT, Stock INTEGER,
                ExpiryDate TEXT, Price REAL
            );
            CREATE TABLE invoices (
                id TEXT PRIMARY KEY, invoice_date TEXT,
                subtotal REAL, vat_amount REAL, grand_total REAL
            );
        """)
        for p in [
            ("A", "OK",           50, far,     1.0),
            ("B", "Low",           3, far,     2.0),
            ("C", "Expired",      20, expired, 3.0),
            ("D", "Near-Expiry",  15, near,    4.0),
            ("E", "Far",           0, far,     5.0),
        ]:
            conn.execute("INSERT INTO ProductMaster VALUES (?,?,?,?,?)", p)
        conn.execute(
            "INSERT INTO invoices VALUES ('I1', date('now'), 10, 1.5, 11.5)")
        conn.execute(
            "INSERT INTO invoices VALUES ('I2', date('now'), 20, 3.0, 23.0)")
        conn.commit()
        conn.close()
        r = load_dashboard(str(db), threshold=10, alert_days=30)
        assert r.ok
        s = r.snapshot
        assert s.total_products == 5
        assert s.low_stock_count == 2
        assert s.expiry_alert_count == 2
        assert s.revenue_today == 34.50
        assert s.vat_today == 4.50
        assert s.invoice_count == 2

    def test_default_alert_days_includes_20_excludes_31(self, tmp_path):
        """alert_days=30 default: product expiring in 20 days IS included
        as an expiry alert; product expiring in 31 days is NOT an expiry
        alert (both have stock above default threshold=10 so they don't
        appear as low-stock)."""
        near = _date_str(20)
        far = _date_str(31)
        db = tmp_path / "test.db"
        _make_db(str(db), [
            ("A", "Near",  99, near, 1.0),
            ("B", "Far",   99, far,  1.0),
        ])
        r = load_dashboard(str(db))
        assert r.ok
        s = r.snapshot
        assert s.expiry_alert_count == 1  # only the 20-day product
        barcodes = {cp.barcode for cp in s.critical_products}
        assert "A" in barcodes
        assert "B" not in barcodes

    def test_capped_at_20(self, tmp_path):
        db = tmp_path / "test.db"
        prods = [(f"B{i:06d}", f"P{i}", 0, "2027-12-31", 1.0) for i in range(30)]
        _make_db(str(db), prods)
        r = load_dashboard(str(db), threshold=5, alert_days=30)
        assert r.ok
        assert len(r.snapshot.critical_products) == 20

    def test_fallback_thresholds(self, tmp_path):
        db = tmp_path / "test.db"
        _make_db(str(db), [("A", "Low", 8, "2027-12-31", 1.0)])
        r = load_dashboard(str(db))  # threshold=10 default
        assert r.ok
        assert r.snapshot.low_stock_count == 1

    def test_multi_reason(self, tmp_path):
        """Product both expired AND low-stock gets multiple reasons."""
        expired = _date_str(-5)
        db = tmp_path / "test.db"
        _make_db(str(db), [("X", "Bad", 3, expired, 5.0)])
        r = load_dashboard(str(db), threshold=10, alert_days=30)
        assert r.ok
        cp = r.snapshot.critical_products[0]
        reasons = cp.reasons
        assert any("Ληγμένο" in rsn for rsn in reasons)
        assert any("Χαμηλό απόθεμα" in rsn for rsn in reasons)

    def test_no_write_sql(self):
        """data_source.py contains no write-SQL keywords at statement start."""
        import ast
        src = os.path.join(os.path.dirname(__file__), "..", "qt_app", "data_source.py")
        tree = ast.parse(open(src, encoding="utf-8").read())
        forbidden = {"INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                      "CREATE ", "REPLACE ", "TRUNCATE "}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper()
                for kw in forbidden:
                    if kw in upper and (
                            upper.startswith(kw) or f"\n{kw}" in upper):
                        pytest.fail(f"Forbidden SQL '{kw.strip()}': {node.value[:80]!r}")
        # If we get here, no write SQL found
        pass
        """The data source opens exactly one connection per load_dashboard().
        Verified structurally: _build_reasons_from_flags is pure Python
        (no sqlite3 calls), and the per-row loop does not call sqlite3."""
        import inspect
        from qt_app import data_source as ds
        src = inspect.getsource(ds._build_reasons_from_flags)
        assert "sqlite3" not in src
        assert "connect" not in src

    def test_dashboard_closes_connection_on_success(self, tmp_path):
        """load_dashboard() closes its connection after a successful load."""
        import sqlite3 as _sqlite3
        from qt_app.data_source import load_dashboard, _connect_ro
        closed = []

        class _TrackedConn:
            def __init__(self, real):
                self._real = real
            def cursor(self):
                return self._real.cursor()
            def close(self):
                closed.append(True)
                self._real.close()
            def __getattr__(self, name):
                return getattr(self._real, name)

        original = _connect_ro
        def _tracked(path):
            return _TrackedConn(original(path))

        import qt_app.data_source as ds
        ds._connect_ro = _tracked
        try:
            db = tmp_path / "test.db"
            _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
            r = load_dashboard(str(db))
            assert r.ok
            assert len(closed) == 1, "connection not closed on success"
        finally:
            ds._connect_ro = original

    def test_dashboard_closes_connection_on_error(self, tmp_path):
        """load_dashboard() closes its connection even on SQLite failure."""
        import sqlite3 as _sqlite3
        from qt_app.data_source import load_dashboard, _connect_ro
        closed = []

        class _TrackedConn:
            def __init__(self, real):
                self._real = real
            def cursor(self):
                c = self._real.cursor()
                # Return a cursor that blows up on execute
                class _Bomb:
                    def execute(self, *a, **kw):
                        raise _sqlite3.DatabaseError("boom")
                    def fetchone(self):
                        return None
                return _Bomb()
            def close(self):
                closed.append(True)
                self._real.close()
            def __getattr__(self, name):
                return getattr(self._real, name)

        original = _connect_ro
        def _tracked(path):
            return _TrackedConn(original(path))

        import qt_app.data_source as ds
        ds._connect_ro = _tracked
        try:
            db = tmp_path / "test.db"
            _make_db(str(db), [("A", "X", 1, "2027-01-01", 1.0)])
            r = load_dashboard(str(db))
            assert not r.ok
            assert len(closed) == 1, "connection not closed on error"
        finally:
            ds._connect_ro = original


# ═══════════════════════════════════════════════════════════════════════
# Supplier Reorder Candidates (P3.1)
# ═══════════════════════════════════════════════════════════════════════


def _make_reorder_db(
    path: str,
    products: list[tuple],
    suppliers: list[tuple] | None = None,
) -> None:
    """Create a temp DB with ProductMaster and optional suppliers."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE ProductMaster (
            Barcode      TEXT PRIMARY KEY,
            Name         TEXT NOT NULL,
            Stock        INTEGER NOT NULL,
            ExpiryDate   TEXT NOT NULL,
            Price        REAL NOT NULL,
            supplier_id  INTEGER
        )
    """)
    if suppliers is not None:
        conn.execute("""
            CREATE TABLE suppliers (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
        for sup in suppliers:
            conn.execute("INSERT INTO suppliers (id, name) VALUES (?, ?)", sup)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?,?,?,?,?,?)", p)
    conn.commit()
    conn.close()


class TestSupplierReorderCandidates:

    # ── imports ───────────────────────────────────────────────────────

    @staticmethod
    def _load(**kw):
        from qt_app.data_source import load_supplier_reorder_candidates
        return load_supplier_reorder_candidates(**kw)

    # ── basic grouping ────────────────────────────────────────────────

    def test_groups_by_supplier(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha",   3, "2027-06-01", 10.0, 1),
            ("B", "Beta",    5, "2027-08-01", 20.0, 1),
            ("C", "Gamma",   2, "2027-07-01", 15.0, 2),
        ], suppliers=[(1, "Φάρμακο ΑΕ"), (2, "MediCorp")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok, r.error_message
        assert len(r.groups) == 2
        assert r.groups[0].supplier_name == "MediCorp"        # alphabetically first
        assert r.groups[1].supplier_name == "Φάρμακο ΑΕ"
        assert len(r.groups[0].products) == 1
        assert r.groups[0].products[0].barcode == "C"
        assert len(r.groups[1].products) == 2
        assert [p.barcode for p in r.groups[1].products] == ["A", "B"]
        assert len(r.unassigned) == 0

    def test_deterministic_product_order(self, tmp_path):
        """Products within a group sorted by name then barcode."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("Z", "Zeta",   2, "2027-01-01", 5.0, 1),
            ("A", "Alpha",  3, "2027-01-01", 5.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert [p.barcode for p in r.groups[0].products] == ["A", "Z"]

    def test_deterministic_supplier_order(self, tmp_path):
        """Suppliers with same name tie-broken by id."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("X", "Xray", 1, "2027-01-01", 1.0, 10),
            ("Y", "Yankee", 1, "2027-01-01", 1.0, 5),
        ], suppliers=[(5, "SameName"), (10, "SameName")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 2
        # id 5 before id 10
        assert r.groups[0].supplier_id == 5
        assert r.groups[1].supplier_id == 10

    # ── threshold boundary ────────────────────────────────────────────

    def test_exact_threshold_included(self, tmp_path):
        """Stock == threshold IS included."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 5, "2027-01-01", 1.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=5)
        assert r.ok
        assert len(r.groups) == 1
        assert r.groups[0].products[0].barcode == "A"

    def test_above_threshold_excluded(self, tmp_path):
        """Stock > threshold is excluded."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 6, "2027-01-01", 1.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=5)
        assert r.ok
        assert len(r.groups) == 0

    def test_custom_threshold_defaults(self, tmp_path):
        """Default threshold=10 works."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 9, "2027-01-01", 1.0, 1),
            ("B", "Beta",  11, "2027-01-01", 1.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db)
        assert r.ok
        assert len(r.groups[0].products) == 1
        assert r.groups[0].products[0].barcode == "A"

    # ── unassigned: no supplier_id ────────────────────────────────────

    def test_null_supplier_id_unassigned(self, tmp_path):
        """Product with supplier_id=NULL goes to unassigned."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 3, "2027-01-01", 5.0, None),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 0
        assert len(r.unassigned) == 1
        assert r.unassigned[0].barcode == "A"
        assert r.unassigned[0].reason == "Χωρίς προμηθευτή"

    def test_missing_supplier_id_column_all_unassigned(self, tmp_path):
        """No supplier_id column → every product is unassigned."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE ProductMaster (
                Barcode TEXT, Name TEXT, Stock INT,
                ExpiryDate TEXT, Price REAL
            )
        """)
        conn.execute("INSERT INTO ProductMaster VALUES ('A','Alpha',3,'2027-01-01',5.0)")
        conn.commit()
        conn.close()
        r = self._load(db_path=str(db), threshold=10)
        assert r.ok, r.error_message
        assert len(r.groups) == 0
        assert len(r.unassigned) == 1
        assert r.unassigned[0].reason == "Χωρίς προμηθευτή"

    # ── unassigned: missing supplier (orphaned FK) ────────────────────

    def test_orphaned_supplier_fk_unassigned(self, tmp_path):
        """supplier_id points to non-existent row → unassigned."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 3, "2027-01-01", 5.0, 999),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 0
        assert len(r.unassigned) == 1
        assert r.unassigned[0].barcode == "A"
        assert r.unassigned[0].reason == "Ο προμηθευτής δεν υπάρχει"

    def test_no_suppliers_table_orphans_all(self, tmp_path):
        """suppliers table missing → all assigned products are orphaned."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 3, "2027-01-01", 5.0, 1),
            ("B", "Beta",  2, "2027-01-01", 5.0, None),
        ], suppliers=None)
        r = self._load(db_path=db, threshold=10)
        assert r.ok, r.error_message
        assert len(r.groups) == 0
        assert len(r.unassigned) == 2
        reasons = {u.barcode: u.reason for u in r.unassigned}
        assert reasons["A"] == "Ο προμηθευτής δεν υπάρχει"
        assert reasons["B"] == "Χωρίς προμηθευτή"

    # ── empty results ─────────────────────────────────────────────────

    def test_no_low_stock_products(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 50, "2027-01-01", 5.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 0
        assert len(r.unassigned) == 0

    def test_empty_product_table(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 0
        assert len(r.unassigned) == 0

    # ── mix of assigned and unassigned ────────────────────────────────

    def test_mixed_assigned_unassigned(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha",  3, "2027-01-01", 5.0, 1),
            ("B", "Beta",   4, "2027-01-01", 5.0, None),
            ("C", "Gamma",  5, "2027-01-01", 5.0, 999),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert len(r.groups) == 1
        assert len(r.groups[0].products) == 1
        assert r.groups[0].products[0].barcode == "A"
        assert len(r.unassigned) == 2
        un_barcodes = {u.barcode for u in r.unassigned}
        assert un_barcodes == {"B", "C"}

    # ── data integrity ────────────────────────────────────────────────

    def test_candidate_has_all_fields(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("BC", "Test Product", 3, "2027-06-15", 12.50, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        c = r.groups[0].products[0]
        assert c.barcode == "BC"
        assert c.name == "Test Product"
        assert c.stock == 3
        assert c.threshold == 10
        assert c.expiry_date == "2027-06-15"
        assert c.price == 12.50

    def test_blank_expiry_renders_dash(self, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [
            ("A", "Alpha", 3, "", 5.0, 1),
        ], suppliers=[(1, "S1")])
        r = self._load(db_path=db, threshold=10)
        assert r.ok
        assert r.groups[0].products[0].expiry_date == "—"

    # ── error handling ────────────────────────────────────────────────

    def test_missing_db_returns_error(self, tmp_path):
        r = self._load(db_path=str(tmp_path / "gone.db"))
        assert not r.ok
        assert "Αδυναμία" in r.error_message
        assert len(r.groups) == 0
        assert len(r.unassigned) == 0

    def test_error_has_empty_containers(self, tmp_path):
        r = self._load(db_path=str(tmp_path / "nope.db"))
        assert not r.ok
        assert r.groups == ()
        assert r.unassigned == ()
        assert len(r.error_message) > 0

    # ── read-only safety ──────────────────────────────────────────────

    def test_no_write_sql(self):
        """load_supplier_reorder_candidates contains no DML/DDL."""
        import inspect, os
        from qt_app import data_source as ds
        src = inspect.getsource(ds.load_supplier_reorder_candidates)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE "]
        for pat in patterns:
            assert pat not in src.upper(), f"Forbidden '{pat}' in reorder source"

    def test_connection_closed_on_success(self, tmp_path):
        from qt_app.data_source import load_supplier_reorder_candidates, _connect_ro
        closed = []

        class _TrackedConn:
            def __init__(self, real):
                self._real = real
            def cursor(self):
                return self._real.cursor()
            def close(self):
                closed.append(True)
                self._real.close()
            def __getattr__(self, name):
                return getattr(self._real, name)

        original = _connect_ro
        def _tracked(path):
            return _TrackedConn(original(path))

        import qt_app.data_source as ds
        ds._connect_ro = _tracked
        try:
            db = str(tmp_path / "t.db")
            _make_reorder_db(db, [("A", "X", 1, "2027-01-01", 1.0, 1)],
                             suppliers=[(1, "S1")])
            r = load_supplier_reorder_candidates(db)
            assert r.ok
            assert len(closed) == 1, "connection not closed on success"
        finally:
            ds._connect_ro = original

    def test_connection_closed_on_error(self, tmp_path):
        from qt_app.data_source import load_supplier_reorder_candidates, _connect_ro
        closed = []

        class _TrackedConn:
            def __init__(self, real):
                self._real = real
            def cursor(self):
                c = self._real.cursor()
                class _Bomb:
                    def execute(self, *a, **kw):
                        raise sqlite3.DatabaseError("boom")
                    def fetchone(self):
                        return None
                    def fetchall(self):
                        return []
                return _Bomb()
            def close(self):
                closed.append(True)
                self._real.close()
            def __getattr__(self, name):
                return getattr(self._real, name)

        original = _connect_ro
        def _tracked(path):
            return _TrackedConn(original(path))

        import qt_app.data_source as ds
        ds._connect_ro = _tracked
        try:
            db = str(tmp_path / "t.db")
            _make_reorder_db(db, [("A", "X", 1, "2027-01-01", 1.0, 1)],
                             suppliers=[(1, "S1")])
            r = load_supplier_reorder_candidates(db)
            assert not r.ok
            assert len(closed) == 1, "connection not closed on error"
        finally:
            ds._connect_ro = original
