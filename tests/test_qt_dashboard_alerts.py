"""Offscreen Qt tests for Dashboard Daily Alerts (P2.2).

Real event delivery, bounded waiting, no manual lifecycle state mutations.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QTableWidgetItem, QMainWindow,
)

from qt_app.pages.dashboard_page import DashboardPage
from qt_app.main_window import MainWindow


# ═══════════════════════════════════════════════════════════════════════
# Bounded Qt event helper
# ═══════════════════════════════════════════════════════════════════════

def _pump_until(predicate, *, timeout_s: float = 3.0, tick_s: float = 0.05):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return
        time.sleep(tick_s)
    raise AssertionError(
        f"_pump_until: predicate still False after {timeout_s:.1f}s")


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _make_db(path: str, products: list[tuple]) -> None:
    """Create minimal schema and seed products. Each tuple:
    (barcode, name, stock, expiry_date, price)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, reason TEXT NOT NULL,
            change_amount INTEGER, source TEXT,
            operator TEXT DEFAULT 'Σύστημα'
        );
        CREATE TABLE SystemConfig (Key TEXT PRIMARY KEY, Value TEXT);
        CREATE TABLE invoices (id TEXT PRIMARY KEY, invoice_date TEXT,
            subtotal REAL, vat_amount REAL, grand_total REAL);
        CREATE TABLE invoice_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT, barcode TEXT, name TEXT, quantity INTEGER,
            price REAL);
        CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, amka TEXT, phone TEXT);
    """)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?, ?, ?, ?, ?, NULL)", p)
    conn.commit()
    conn.close()


@pytest.fixture
def dash_page(qapp):
    """Create a DashboardPage with a temp DB, clean up after."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="dash_test_")
    os.close(fd)
    _make_db(path, [])
    page = DashboardPage(
        db_service=None,
        config={"db_path": path, "low_stock_threshold": 5,
                "expiry_alert_days": 30},
    )
    page._db_path_real = path
    yield page
    page.shutdown()
    page.close()
    page.deleteLater()
    for ext in ("", "-wal", "-shm"):
        p = Path(path + ext)
        if p.exists():
            p.unlink()


@pytest.fixture
def dash_with_products(qapp, monkeypatch):
    """Dashboard page with 3 alert products of different types."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="dash_test_")
    os.close(fd)
    from datetime import date, timedelta
    _d = lambda o: (date.today() + timedelta(days=o)).isoformat()
    _make_db(path, [
        ("EXP", "Expired Product",   50, _d(-3), 10.0),
        ("NEAR","Near Expiry",       50, _d(5),  8.0),
        ("LOW", "Low Stock",          2, _d(365), 5.0),
        ("OK",  "Normal Product",    50, _d(365), 12.0),
    ])

    # Monkeypatch _connect_ro in data_source to handle the temp path
    import qt_app.data_source as ds
    monkeypatch.setattr(ds, "_connect_ro", _connect_ro_factory())

    page = DashboardPage(
        db_service=None,
        config={"db_path": path, "low_stock_threshold": 5,
                "expiry_alert_days": 30},
    )
    page._db_path_real = path
    yield page
    page.shutdown()
    page.close()
    page.deleteLater()
    for ext in ("", "-wal", "-shm"):
        p = Path(path + ext)
        if p.exists():
            p.unlink()


def _connect_ro_factory():
    """Return a _connect_ro that works with the temp_db fixture."""
    import sqlite3
    def _connect_ro(db_path: str) -> sqlite3.Connection:
        path = db_path.replace("\\", "/")
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    return _connect_ro


# ═══════════════════════════════════════════════════════════════════════

class TestDashboardDailyAlerts:

    def test_empty_db_shows_no_alerts(self, dash_page):
        """Empty DB: counts = 0, state shows 'καμία ειδοποίηση'."""
        _pump_until(lambda: not dash_page._loading)
        assert "καμία" in dash_page._state_lbl.text().lower()

    def test_counts_rendered_for_alert_types(self, dash_with_products):
        """Three alert types: low=1, near=1, expired=1."""
        _pump_until(lambda: not dash_with_products._loading)

        assert dash_with_products._lbl_alert_low.text() == "1"
        assert dash_with_products._lbl_alert_near.text() == "1"
        assert dash_with_products._lbl_alert_exp.text() == "1"

    def test_reasons_rendered_in_table(self, dash_with_products):
        """Table rows show Greek reason labels."""
        _pump_until(lambda: not dash_with_products._loading)

        # Build a dict of barcode → reason text
        reason_map = {}
        for r in range(dash_with_products._alerts_table.rowCount()):
            bc = dash_with_products._alerts_table.item(r, 0).text()
            rs = dash_with_products._alerts_table.item(r, 5).text()
            reason_map[bc] = rs

        assert "Ληγμένο" in reason_map.get("EXP", "")
        assert "Λήγει σύντομα" in reason_map.get("NEAR", "")
        assert "Χαμηλό απόθεμα" in reason_map.get("LOW", "")

    def test_filter_changes_reload_data(self, dash_with_products):
        """Changing filter to 'expired' shows only expired."""
        _pump_until(lambda: not dash_with_products._loading)

        # Switch to expired filter
        for i in range(dash_with_products._filter_combo.count()):
            if dash_with_products._filter_combo.itemText(i) == "Ληγμένα":
                dash_with_products._filter_combo.setCurrentIndex(i)
                break

        _pump_until(lambda: not dash_with_products._loading)

        rows = dash_with_products._alerts_table.rowCount()
        assert rows == 1
        bc = dash_with_products._alerts_table.item(0, 0).text()
        assert bc == "EXP"

    def test_pagination_shows_page_label(self, dash_with_products):
        """Page label is populated after load."""
        _pump_until(lambda: not dash_with_products._loading)
        lbl = dash_with_products._page_lbl.text()
        assert "Σελίδα" in lbl
        assert "ειδοποιήσεις" in lbl

    def test_selection_gates_open_button(self, dash_with_products):
        """'Άνοιγμα στην Αποθήκη' enabled only with single selection."""
        _pump_until(lambda: not dash_with_products._loading)
        tbl = dash_with_products._alerts_table

        # Nothing selected → disabled
        tbl.clearSelection()
        dash_with_products._on_alert_selection_changed()
        assert not dash_with_products._open_inv_btn.isEnabled()

        # Select one row → enabled
        tbl.selectRow(0)
        dash_with_products._on_alert_selection_changed()
        assert dash_with_products._open_inv_btn.isEnabled()

        # Select all → disabled
        tbl.selectAll()
        dash_with_products._on_alert_selection_changed()
        assert not dash_with_products._open_inv_btn.isEnabled()

    def test_double_click_opens_barcode(self, dash_with_products,
                                         monkeypatch):
        """Double-clicking a row calls open_inventory_with_barcode."""
        _pump_until(lambda: not dash_with_products._loading)

        opened = []

        class _FakeWindow:
            def open_inventory_with_barcode(self, bc):
                opened.append(bc)

        monkeypatch.setattr(dash_with_products, "window", lambda: _FakeWindow())

        # Double-click first row
        barcode = dash_with_products._alerts_table.item(0, 0).text()
        dash_with_products._on_alert_double_click(0, 0)

        assert len(opened) == 1
        assert opened[0] == barcode

    def test_button_opens_barcode(self, dash_with_products, monkeypatch):
        """Clicking 'Άνοιγμα στην Αποθήκη' with selection navigates."""
        _pump_until(lambda: not dash_with_products._loading)
        tbl = dash_with_products._alerts_table

        opened = []

        class _FakeWindow:
            def open_inventory_with_barcode(self, bc):
                opened.append(bc)

        monkeypatch.setattr(dash_with_products, "window", lambda: _FakeWindow())

        tbl.selectRow(0)
        dash_with_products._on_alert_selection_changed()
        expected_bc = tbl.item(0, 0).text()
        dash_with_products._on_open_in_inventory()

        assert len(opened) == 1
        assert opened[0] == expected_bc


class TestDashboardNavigationToInventory:

    def test_mainwindow_opens_inventory_with_barcode(self, qapp, tmp_path,
                                                      monkeypatch):
        """MainWindow.open_inventory_with_barcode navigates and calls
        focus_barcode on the inventory page."""
        from PySide6.QtWidgets import QWidget, QLabel

        fd, path = tempfile.mkstemp(suffix=".db", prefix="nav_test_")
        os.close(fd)
        _make_db(path, [("BC001", "Test", 10, "2027-12-31", 5.0)])

        focused = []

        class _FakeInv(QWidget):
            def __init__(self, db_service=None, config=None, parent=None):
                super().__init__(parent)
            def focus_barcode(self, bc):
                focused.append(bc)
            def shutdown(self):
                return True

        class _FakeDash(QWidget):
            def __init__(self, db_service=None, config=None, parent=None):
                super().__init__(parent)
            def shutdown(self):
                return True

        import qt_app.main_window as mw_mod
        monkeypatch.setattr(mw_mod, "PAGE_CLASSES", {
            "inventory": _FakeInv,
            "dashboard": _FakeDash,
        })

        mw = MainWindow(db_service=None, config={"db_path": path})
        mw.open_inventory_with_barcode("BC001")

        assert focused == ["BC001"]
        mw.close()
        mw.deleteLater()

        for ext in ("", "-wal", "-shm"):
            p = Path(path + ext)
            if p.exists():
                p.unlink()
