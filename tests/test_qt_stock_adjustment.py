"""Offscreen Qt tests for stock adjustment UI on Inventory page.

Tests: button gating, dialog validation, real cancellation via
_on_adjust_stock, real success refresh, real Qt-event-driven lifecycle,
real worker shutdown, and ProductDialog stock read-only gate.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QTableWidgetItem, QDialog, QMessageBox, QLabel,
)

from qt_app.pages.inventory_page import (
    InventoryPage, StockAdjustmentDialog, ProductDialog,
)
from infrastructure.stock_adjustment_service import (
    StockAdjustmentResult, adjust_stock,
)


# ═══════════════════════════════════════════════════════════════════════
# Bounded Qt event-processing helper for tests only
# ═══════════════════════════════════════════════════════════════════════

def _pump_until(predicate, *, timeout_s: float = 3.0, tick_s: float = 0.05):
    """Process Qt events in bounded ticks until *predicate()* is True.

    Fails with a descriptive AssertionError if the predicate is still
    False after *timeout_s* seconds.
    """
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


@pytest.fixture
def temp_db_path():
    """Create a minimal SQLite DB with products for adjustment tests."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="adj_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, reason TEXT NOT NULL,
            change_amount INTEGER, source TEXT,
            operator TEXT DEFAULT 'Σύστημα'
        );
    """)
    conn.execute(
        "INSERT INTO ProductMaster VALUES "
        "('TEST001', 'Test Product', 50, '2027-12-31', 10.50, NULL)")
    conn.execute(
        "INSERT INTO ProductMaster VALUES "
        "('TEST002', 'Another', 10, '2027-12-31', 5.0, NULL)")
    conn.commit()
    conn.close()
    yield path
    for ext in ("", "-wal", "-shm"):
        p = Path(str(path) + ext)
        if p.exists():
            p.unlink()


@pytest.fixture
def inventory_page(qapp, temp_db_path):
    """Create an InventoryPage with the temp DB."""
    page = InventoryPage(
        db_service=None,
        config={"db_path": temp_db_path},
    )
    yield page
    page.shutdown()
    page.close()
    page.deleteLater()


# ── Fake QMessageBox helper ─────────────────────────────────────────────

class _FakeQMB:
    Yes = 16384
    No = 65536

    def __init__(self):
        self.warnings = []
        self.infos = []
        self._question_ret = self.Yes

    def warning(self, parent, title, msg):
        self.warnings.append(msg)
        return self.No

    def critical(self, parent, title, msg):
        self.warnings.append(msg)
        return self.No

    def information(self, parent, title, msg):
        self.infos.append(msg)
        return None

    def question(self, parent, title, text, buttons, **kw):
        return self._question_ret


def _install_qmb(monkeypatch, question_ret=_FakeQMB.Yes):
    import qt_app.pages.inventory_page as ip_mod
    fk = _FakeQMB()
    fk._question_ret = question_ret
    monkeypatch.setattr(ip_mod, "QMessageBox", fk)
    return fk


# ═══════════════════════════════════════════════════════════════════════
# Test: button selection gating
# ═══════════════════════════════════════════════════════════════════════

class TestAdjustmentButtonGating:

    def test_button_disabled_initially(self, inventory_page):
        assert not inventory_page._adj_btn.isEnabled()

    def test_button_enabled_on_single_row_selection(self, inventory_page):
        inventory_page._table.setRowCount(2)
        inventory_page._table.setItem(0, 0, QTableWidgetItem("A"))
        inventory_page._table.setItem(1, 0, QTableWidgetItem("B"))
        inventory_page._table.selectRow(0)
        assert inventory_page._adj_btn.isEnabled()

    def test_button_disabled_on_multi_row_selection(self, inventory_page):
        inventory_page._table.setRowCount(2)
        inventory_page._table.setItem(0, 0, QTableWidgetItem("A"))
        inventory_page._table.setItem(1, 0, QTableWidgetItem("B"))
        inventory_page._table.selectAll()
        assert not inventory_page._adj_btn.isEnabled()

    def test_button_disabled_on_no_selection(self, inventory_page):
        inventory_page._table.setRowCount(2)
        inventory_page._table.setItem(0, 0, QTableWidgetItem("A"))
        inventory_page._table.clearSelection()
        assert not inventory_page._adj_btn.isEnabled()


# ═══════════════════════════════════════════════════════════════════════
# Test: dialog validation and reason behavior
# ═══════════════════════════════════════════════════════════════════════

class TestAdjustmentDialog:

    def test_dialog_creation_shows_barcode_and_name(self, qapp):
        dlg = StockAdjustmentDialog(
            barcode="TEST001", name="Test Product", current_stock=50)
        assert "Διόρθωση Αποθέματος" in dlg.windowTitle()
        dlg.close()
        dlg.deleteLater()

    def test_dialog_result_returns_data(self, qapp):
        dlg = StockAdjustmentDialog(
            barcode="MYCODE", name="Some Product", current_stock=42)
        dlg._counted_spin.setValue(60)
        dlg._reason_combo.setCurrentText("Απογραφή")
        data = dlg.result()
        assert data["barcode"] == "MYCODE"
        assert data["expected_stock"] == 42
        assert data["counted_stock"] == 60
        assert data["reason"] == "Απογραφή"
        dlg.close()
        dlg.deleteLater()

    def test_other_reason_includes_free_text(self, qapp):
        dlg = StockAdjustmentDialog(
            barcode="X", name="Y", current_stock=10)
        dlg._reason_combo.setCurrentText("Άλλη αιτία")
        dlg._other_edit.setText("Λάθος καταμέτρηση")
        data = dlg.result()
        assert data["reason"] == "Άλλη αιτία: Λάθος καταμέτρηση"
        dlg.close()
        dlg.deleteLater()

    def test_other_reason_requires_text(self, qapp, monkeypatch):
        fk = _install_qmb(monkeypatch)

        dlg = StockAdjustmentDialog(
            barcode="X", name="Y", current_stock=10)
        dlg._reason_combo.setCurrentText("Άλλη αιτία")
        dlg._other_edit.setText("")
        dlg._on_accept()
        assert len(fk.warnings) == 1
        assert "περιγράψετε" in fk.warnings[0].lower()
        dlg.close()
        dlg.deleteLater()

    def test_difference_preview_updates(self, qapp):
        dlg = StockAdjustmentDialog(
            barcode="X", name="Y", current_stock=50)
        dlg._counted_spin.setValue(30)
        assert dlg._diff_lbl.text() == "-20"
        dlg._counted_spin.setValue(80)
        assert dlg._diff_lbl.text() == "+30"
        dlg._counted_spin.setValue(50)
        assert dlg._diff_lbl.text() == "+0"
        dlg.close()
        dlg.deleteLater()

    def test_dialog_has_no_editable_product_fields(self, qapp):
        """Adjustment dialog must have no name, price, supplier, expiry,
        or VAT editable controls — only counted stock, reason, buttons."""
        dlg = StockAdjustmentDialog(
            barcode="X", name="Y", current_stock=50)
        dlg.show()

        all_label_text = ""
        for w in dlg.findChildren(QLabel):
            try:
                all_label_text += w.text() + " "
            except Exception:
                pass

        assert "Τιμή" not in all_label_text, "Price label found"
        assert "ΦΠΑ" not in all_label_text, "VAT label found"
        assert "Λήξης" not in all_label_text, "Expiry label found"
        assert "Προμηθευτής" not in all_label_text, "Supplier label found"

        spinners = [w for w in dlg.findChildren(type(dlg._counted_spin))
                    if w is not dlg._counted_spin]
        assert len(spinners) == 0, (
            "Only one QSpinBox (counted stock) should exist")

        assert dlg._reason_combo is not None

        dlg.close()
        dlg.deleteLater()


# ═══════════════════════════════════════════════════════════════════════
# Test: cancellation via _on_adjust_stock with fake dialog
# ═══════════════════════════════════════════════════════════════════════

class TestAdjustmentCancellation:

    def test_cancel_confirm_performs_no_write(self, inventory_page,
                                              monkeypatch, temp_db_path):
        """Populate row, fake accepted dialog, QMB.question→No.
        Assert _run_adjustment not called, stock unchanged."""
        import qt_app.pages.inventory_page as ip_mod

        inventory_page._table.setRowCount(1)
        inventory_page._table.setItem(0, 0, QTableWidgetItem("TEST001"))
        inventory_page._table.setItem(0, 1, QTableWidgetItem("Test Product"))
        inventory_page._table.setItem(0, 2, QTableWidgetItem("50"))
        inventory_page._table.setItem(0, 3, QTableWidgetItem("2027-12-31"))
        inventory_page._table.setItem(0, 4, QTableWidgetItem("€10.50"))
        inventory_page._table.selectRow(0)

        class _FakeAcceptedDialog:
            @staticmethod
            def exec():
                return QDialog.Accepted
            def result(self):
                return {
                    "barcode": "TEST001",
                    "expected_stock": 50,
                    "counted_stock": 60,
                    "reason": "Απογραφή",
                }

        monkeypatch.setattr(
            ip_mod, "StockAdjustmentDialog",
            lambda *a, **kw: _FakeAcceptedDialog())

        fk = _install_qmb(monkeypatch, question_ret=_FakeQMB.No)

        inventory_page._on_adjust_stock()

        assert not inventory_page._adj_loading
        assert inventory_page._adj_thread is None
        assert inventory_page._adj_worker is None

        stock = _read_stock(temp_db_path, "TEST001")
        assert stock == 50


# ═══════════════════════════════════════════════════════════════════════
# Test: successful completion
# ═══════════════════════════════════════════════════════════════════════

class TestAdjustmentSuccess:

    def test_success_refresh_is_called(self, inventory_page, monkeypatch):
        """_on_adjust_done with ok=True calls refresh + _refresh_dashboard."""
        fk = _install_qmb(monkeypatch)

        refresh_calls = []
        dashboard_calls = []

        def _fake_refresh():
            refresh_calls.append(1)
        def _fake_dash():
            dashboard_calls.append(1)

        monkeypatch.setattr(inventory_page, "refresh", _fake_refresh,
                            raising=False)
        monkeypatch.setattr(inventory_page, "_refresh_dashboard",
                            _fake_dash, raising=False)

        inventory_page._on_adjust_done(StockAdjustmentResult(
            ok=True, message="Επιτυχία: διορθώθηκε"))

        assert len(refresh_calls) == 1
        assert len(dashboard_calls) == 1
        assert len(fk.infos) == 1
        assert "διορθώθηκε" in fk.infos[0]

    def test_failure_shows_warning_not_info(self, inventory_page,
                                             monkeypatch):
        """_on_adjust_done with ok=False shows warning, not info."""
        fk = _install_qmb(monkeypatch)

        inventory_page._on_adjust_done(StockAdjustmentResult(
            ok=False, message="Σφάλμα: κάτι πήγε στραβά"))

        assert len(fk.warnings) == 1
        assert len(fk.infos) == 0

    def test_result_no_change_flag(self):
        r = StockAdjustmentResult(ok=True, message="No change",
                                  no_change=True)
        assert r.no_change and r.ok
        r2 = StockAdjustmentResult(ok=True, message="Changed",
                                   no_change=False)
        assert not r2.no_change and r2.ok


# ═══════════════════════════════════════════════════════════════════════
# Test: real event-driven adjustment lifecycle
# ═══════════════════════════════════════════════════════════════════════

class TestRealLifecycle:

    def test_full_adjustment_lifecycle(self, inventory_page, monkeypatch,
                                        temp_db_path):
        """Call real _run_adjustment against temp DB, pump Qt events
        until production callbacks restore state, assert DB changed."""
        fk = _install_qmb(monkeypatch)

        # Ensure clean starting state (no stale loading flag)
        inventory_page._adj_loading = False
        inventory_page._write_loading = False
        inventory_page._create_btn.setEnabled(True)
        inventory_page._edit_btn.setEnabled(True)
        inventory_page._adj_btn.setEnabled(True)
        inventory_page._refresh_btn.setEnabled(True)
        inventory_page._preview_btn.setEnabled(True)

        # Start real worker against the temp DB
        inventory_page._run_adjustment({
            "barcode": "TEST001",
            "expected_stock": 50,
            "counted_stock": 55,
            "reason": "Απογραφή",
        })

        # Production code must have immediately set loading + disabled
        assert inventory_page._adj_loading
        assert not inventory_page._create_btn.isEnabled()
        assert not inventory_page._edit_btn.isEnabled()
        assert not inventory_page._adj_btn.isEnabled()
        assert not inventory_page._refresh_btn.isEnabled()
        assert not inventory_page._preview_btn.isEnabled()
        assert inventory_page._adj_thread is not None
        assert inventory_page._adj_worker is not None

        # Pump real Qt events until the full signal chain completes
        # (finished → _on_adjust_done → quit → _on_adjust_thread_done)
        _pump_until(lambda: inventory_page._adj_thread is None)

        # _on_adjust_done calls refresh() which starts a new read worker.
        # Wait for that inventory refresh chain to finish as well before
        # asserting final control state.
        _pump_until(lambda: not inventory_page._loading
                    and inventory_page._thread is None)

        # Production callbacks must have restored state
        assert not inventory_page._adj_loading
        assert inventory_page._adj_worker is None
        assert inventory_page._adj_thread is None
        assert inventory_page._create_btn.isEnabled()
        assert inventory_page._preview_btn.isEnabled()
        assert inventory_page._refresh_btn.isEnabled()

        # DB must reflect the change
        assert _read_stock(temp_db_path, "TEST001") == 55

        # Exactly one audit row
        audit = _audit_rows(temp_db_path, "TEST001")
        assert len(audit) == 1
        assert audit[0]["change_amount"] == 5
        assert audit[0]["reason"] == "Απογραφή"
        assert audit[0]["old_stock"] == 50
        assert audit[0]["new_stock"] == 55

    def test_adjust_while_loading_is_blocked(self, inventory_page):
        inventory_page._adj_loading = True
        inventory_page._run_adjustment({
            "barcode": "X", "expected_stock": 1,
            "counted_stock": 2, "reason": "Test",
        })
        assert inventory_page._adj_thread is None
        assert inventory_page._adj_worker is None

    def test_adjust_while_write_loading_is_blocked(self, inventory_page):
        inventory_page._write_loading = True
        inventory_page._run_adjustment({
            "barcode": "X", "expected_stock": 1,
            "counted_stock": 2, "reason": "Test",
        })
        assert inventory_page._adj_thread is None
        assert inventory_page._adj_worker is None


# ═══════════════════════════════════════════════════════════════════════
# Test: real worker shutdown lifecycle
# ═══════════════════════════════════════════════════════════════════════

class TestShutdownLifecycle:

    def test_shutdown_with_no_workers_returns_true(self, inventory_page):
        assert inventory_page.shutdown()

    def test_shutdown_while_worker_blocked_then_recovers(self,
                                                          inventory_page,
                                                          monkeypatch):
        """Start blocking worker, shutdown→False, release, pump events,
        shutdown→True with no running thread retained."""
        import qt_app.pages.inventory_page as ip_mod

        _block = threading.Event()
        _started = threading.Event()

        def _blocking_adjust(db_path, req):
            _started.set()
            _block.wait(timeout=5.0)
            return adjust_stock(db_path, req)

        monkeypatch.setattr(ip_mod, "adjust_stock", _blocking_adjust)
        fk = _install_qmb(monkeypatch)

        # Start worker
        inventory_page._run_adjustment({
            "barcode": "TEST001",
            "expected_stock": 50,
            "counted_stock": 55,
            "reason": "Απογραφή",
        })
        assert inventory_page._adj_thread is not None
        assert inventory_page._adj_thread.isRunning()

        # Wait until worker is inside the blocking call
        _started.wait(timeout=2.0)
        assert _started.is_set(), "Worker never started running"

        # First shutdown — worker still blocked, must return False
        result1 = inventory_page.shutdown()
        assert result1 is False, (
            f"Expected shutdown→False while worker blocked, got {result1}")
        # Page must preserve the running thread reference
        assert inventory_page._adj_thread is not None, (
            "Thread reference must be preserved after timeout")
        assert inventory_page._adj_thread.isRunning()

        # Release the blocking operation
        _block.set()

        # Pump Qt events until the worker's completion callbacks fire
        _pump_until(lambda: inventory_page._adj_thread is None)

        # Second shutdown — no workers left, must return True
        result2 = inventory_page.shutdown()
        assert result2 is True, (
            f"Expected shutdown→True after worker completed, got {result2}")
        assert inventory_page._adj_thread is None
        assert inventory_page._adj_worker is None
        assert not inventory_page._adj_loading

    def test_dialog_reject_does_not_crash(self, qapp):
        dlg = StockAdjustmentDialog(
            barcode="X", name="Y", current_stock=10)
        dlg.reject()
        assert dlg.result() is not None
        dlg.close()
        dlg.deleteLater()


# ═══════════════════════════════════════════════════════════════════════
# Test: ProductDialog stock read-only gate
# ═══════════════════════════════════════════════════════════════════════

class TestProductDialogStockGate:

    def test_create_dialog_stock_is_editable(self, qapp, temp_db_path):
        """Creating a new product: stock field must be editable."""
        dlg = ProductDialog(temp_db_path, existing=None)
        assert not dlg._stock_spin.isReadOnly()
        dlg._stock_spin.setValue(30)
        assert dlg._stock_spin.value() == 30
        dlg.close()
        dlg.deleteLater()

    def test_edit_dialog_stock_is_read_only(self, qapp, temp_db_path):
        """Editing existing product: stock field must be read-only."""
        existing = {
            "barcode": "TEST001", "name": "Test Product",
            "stock": 50, "expiry_date": "2027-12-31",
            "price": 10.50, "supplier_id": None,
        }
        dlg = ProductDialog(temp_db_path, existing=existing)
        assert dlg._stock_spin.isReadOnly(), (
            "Stock field must be read-only when editing existing product")
        assert dlg._stock_spin.value() == 50
        data = dlg.get_data()
        assert data["stock"] == 50
        dlg.close()
        dlg.deleteLater()

    def test_edit_dialog_has_stock_hint(self, qapp, temp_db_path):
        """Editing dialog must show the Greek adjustment hint."""
        existing = {
            "barcode": "TEST001", "name": "Test Product",
            "stock": 50, "expiry_date": "2027-12-31",
            "price": 10.50, "supplier_id": None,
        }
        dlg = ProductDialog(temp_db_path, existing=existing)
        assert hasattr(dlg, "_stock_hint"), "Stock hint label missing"
        assert "Διόρθωση Αποθέματος" in dlg._stock_hint.text()
        dlg.close()
        dlg.deleteLater()


# ── Helpers ──────────────────────────────────────────────────────────────

def _read_stock(db_path: str, barcode: str) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    stock = conn.execute(
        "SELECT Stock FROM ProductMaster WHERE Barcode=?",
        (barcode,),
    ).fetchone()[0]
    conn.close()
    return stock


def _audit_rows(db_path: str, barcode: str) -> list:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM stock_movements WHERE barcode=? ORDER BY id",
        (barcode,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
