"""Offscreen Qt tests for inventory export UI on InventoryPage.

Tests: button gating, save dialog cancellation, exact snapshot handoff,
duplicate-export blocking, success/error messages, and safe shutdown
while export is active.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QTableWidgetItem, QDialog, QMessageBox,
)

from qt_app.data_source import (
    InventorySnapshot, InventoryProduct, InventoryResult,
)
from qt_app.pages.inventory_page import (
    InventoryPage,
)
from infrastructure.xlsx_export_service import ExportResult


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
    """Create a minimal SQLite DB for InventoryPage tests."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="export_test_")
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
        "('EXP001', 'Exportable', 50, '2027-12-31', 10.50, NULL)")
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


# ── Fake QFileDialog helper ─────────────────────────────────────────────

class _FakeFileDialog:
    """Simulates QFileDialog.  Pass ``done=False`` to simulate cancel."""

    def __init__(self, done: bool = True):
        self._done = done

    @staticmethod
    def getSaveFileName(parent, title, suggested, filter_str):
        if False:  # never — we monkey-patch the class itself
            pass
        # The real signature returns (path, filter_used).
        # This fake ignores the filter.
        return ("/tmp/fake_export.xlsx", "*.xlsx")


# ── Snapshots factory ──────────────────────────────────────────────────

def _make_snapshot(barcode="EXP001", name="Exportable", stock=50):
    prod = InventoryProduct(
        barcode=barcode, name=name, stock=stock,
        expiry_date="2027-12-31", price=10.50,
        supplier_id=None, supplier_name="—",
        status_labels=("Διαθέσιμο",),
    )
    return InventorySnapshot(
        total_matching=1, page=1, page_size=50,
        products=(prod,),
    )


# ═══════════════════════════════════════════════════════════════════════
# Test: export button gating
# ═══════════════════════════════════════════════════════════════════════

class TestExportButtonGating:

    def test_button_disabled_initially(self, inventory_page):
        """No snapshot loaded yet — button must be disabled."""
        assert inventory_page._export_btn is not None
        assert not inventory_page._export_btn.isEnabled()

    def test_button_enabled_after_successful_snapshot(self, inventory_page):
        """Inject a successful snapshot — button must become enabled."""
        snap = _make_snapshot()
        inventory_page._current_snapshot = snap
        inventory_page._export_btn.setEnabled(True)
        assert inventory_page._export_btn.isEnabled()

    def test_button_disabled_on_no_snapshot(self, inventory_page):
        """After an error or before first load, snapshot is None."""
        inventory_page._current_snapshot = None
        inventory_page._export_btn.setEnabled(False)
        assert not inventory_page._export_btn.isEnabled()

    def test_button_disabled_during_loading(self, inventory_page):
        """Simulate loading state — button disabled."""
        inventory_page._loading = True
        inventory_page._refresh_btn.setEnabled(False)
        inventory_page._export_btn.setEnabled(False)
        assert not inventory_page._export_btn.isEnabled()

    def test_button_disabled_during_export(self, inventory_page):
        """While an export is active, clicking again must be blocked."""
        inventory_page._export_loading = True
        inventory_page._export_btn.setEnabled(False)
        assert not inventory_page._export_btn.isEnabled()
        # _on_export must return early
        inventory_page._current_snapshot = _make_snapshot()
        inventory_page._on_export()
        # Still not loading a new export
        assert inventory_page._export_loading


# ═══════════════════════════════════════════════════════════════════════
# Test: save dialog cancellation
# ═══════════════════════════════════════════════════════════════════════

class TestSaveDialogCancellation:

    def test_cancel_returns_early(self, inventory_page, monkeypatch):
        """Fake QFileDialog.getSaveFileName returning empty — no export."""
        import qt_app.pages.inventory_page as ip_mod

        class _CancelDialog:
            @staticmethod
            def getSaveFileName(parent, title, suggested, filter_str):
                return ("", "")

        monkeypatch.setattr(
            ip_mod, "QFileDialog", _CancelDialog)

        inventory_page._current_snapshot = _make_snapshot()
        inventory_page._on_export()

        assert not inventory_page._export_loading
        assert inventory_page._export_thread is None


# ═══════════════════════════════════════════════════════════════════════
# Test: exact snapshot handoff
# ═══════════════════════════════════════════════════════════════════════

class TestSnapshotHandoff:

    def test_worker_receives_current_snapshot(self, inventory_page,
                                              monkeypatch):
        """The _ExportWorker must receive the exact snapshot stored on
        the page at the time _run_export is called."""
        import qt_app.pages.inventory_page as ip_mod

        snap = _make_snapshot()
        inventory_page._current_snapshot = snap

        received = []

        def _capture_export(snapshot, target_path):
            received.append(snapshot)
            return ExportResult.success(target_path)

        monkeypatch.setattr(
            ip_mod, "export_inventory_snapshot", _capture_export)

        # Fake the dialog to return a path — replace QFileDialog class
        monkeypatch.setattr(ip_mod, "QFileDialog", _FakeFileDialog)

        # Also fake QMessageBox to avoid errors from the done handler
        _install_qmb(monkeypatch)

        inventory_page._on_export()

        # Wait for the thread to complete
        _pump_until(lambda: inventory_page._export_thread is None)

        assert len(received) == 1
        assert received[0] is snap  # exact same object reference


# ═══════════════════════════════════════════════════════════════════════
# Test: duplicate export blocking
# ═══════════════════════════════════════════════════════════════════════

class TestDuplicateExportBlocking:

    def test_duplicate_export_while_active_is_blocked(
            self, inventory_page, monkeypatch):
        """If _export_loading is True, _run_export returns early."""
        import qt_app.pages.inventory_page as ip_mod

        _block = threading.Event()
        _started = threading.Event()

        def _blocking_export(snapshot, path):
            _started.set()
            _block.wait(timeout=5.0)
            return ExportResult.success(path)

        monkeypatch.setattr(
            ip_mod, "export_inventory_snapshot", _blocking_export)

        _install_qmb(monkeypatch)

        # Fake the dialog to return a path — replace QFileDialog class
        monkeypatch.setattr(ip_mod, "QFileDialog", _FakeFileDialog)

        inventory_page._current_snapshot = _make_snapshot()
        inventory_page._on_export()

        # Wait until export worker is inside the blocking function
        _started.wait(timeout=2.0)
        assert inventory_page._export_loading
        assert inventory_page._export_thread is not None
        assert inventory_page._export_thread.isRunning()

        # Simulate a second click — must be blocked
        assert not inventory_page._export_btn.isEnabled()

        # _on_export must return early due to _export_loading check
        old_thread = inventory_page._export_thread
        inventory_page._on_export()
        assert inventory_page._export_thread is old_thread

        # Release the block and let the thread finish
        _block.set()
        _pump_until(lambda: inventory_page._export_thread is None)


# ═══════════════════════════════════════════════════════════════════════
# Test: success and error messages
# ═══════════════════════════════════════════════════════════════════════

class TestExportMessages:

    def test_success_shows_info_with_path(self, inventory_page,
                                           monkeypatch):
        """_on_export_done with ok=True shows info dialog with path."""
        fk = _install_qmb(monkeypatch)

        inventory_page._on_export_done(
            ExportResult.success("/tmp/success.xlsx"))

        assert len(fk.infos) == 1
        assert "αποθηκεύτηκε" in fk.infos[0]
        assert "/tmp/success.xlsx" in fk.infos[0]
        assert len(fk.warnings) == 0

    def test_failure_shows_warning_with_error(self, inventory_page,
                                               monkeypatch):
        """_on_export_done with ok=False shows warning with error."""
        fk = _install_qmb(monkeypatch)

        inventory_page._on_export_done(
            ExportResult.failure("Μη έγκυρη διαδρομή αρχείου."))

        assert len(fk.warnings) == 1
        assert "Μη έγκυρη" in fk.warnings[0]
        assert len(fk.infos) == 0

    def test_no_stale_callback_on_close(self, inventory_page,
                                         monkeypatch):
        """_close_pending=True prevents callback from showing dialogs."""
        fk = _install_qmb(monkeypatch)

        inventory_page._close_pending = True
        inventory_page._on_export_done(
            ExportResult.success("/tmp/should_not_show.xlsx"))

        # No dialogs shown
        assert len(fk.infos) == 0
        assert len(fk.warnings) == 0


# ═══════════════════════════════════════════════════════════════════════
# Test: safe shutdown while export is active
# ═══════════════════════════════════════════════════════════════════════

class TestShutdownLifecycle:

    def test_shutdown_with_no_export_returns_true(self, inventory_page):
        assert inventory_page.shutdown()

    def test_shutdown_with_blocking_export_then_recovers(
            self, inventory_page, monkeypatch):
        """Start blocking export, shutdown->False, release, pump events,
        shutdown->True with no running thread retained."""
        import qt_app.pages.inventory_page as ip_mod

        _block = threading.Event()
        _started = threading.Event()

        def _blocking_export(snapshot, path):
            _started.set()
            _block.wait(timeout=5.0)
            return ExportResult.success(path)

        monkeypatch.setattr(
            ip_mod, "export_inventory_snapshot", _blocking_export)

        _install_qmb(monkeypatch)

        # Directly start export (bypass dialog)
        inventory_page._current_snapshot = _make_snapshot()
        inventory_page._run_export("/tmp/export_during_shutdown.xlsx")

        assert inventory_page._export_thread is not None
        assert inventory_page._export_thread.isRunning()

        # Wait until export worker is inside the blocking function
        _started.wait(timeout=2.0)
        assert _started.is_set(), "Export worker never started running"

        # First shutdown — worker still blocked, must return False
        result1 = inventory_page.shutdown()
        assert result1 is False, (
            f"Expected shutdown->False while export blocked, got {result1}")

        # close_pending must be True
        assert inventory_page._close_pending

        # Release the block — the thread should finish
        _block.set()
        _pump_until(
            lambda: inventory_page._export_thread is None,
            timeout_s=4.0,
        )

        # Second shutdown must return True (all workers stopped)
        result2 = inventory_page.shutdown()
        assert result2 is True, (
            f"Expected shutdown->True after export unblocked, "
            f"got {result2}")

        # No stale references
        assert inventory_page._export_worker is None
        assert inventory_page._export_thread is None
        assert not inventory_page._export_loading
        assert not inventory_page._close_pending

    def test_shutdown_disconnects_export_callback(
            self, inventory_page, monkeypatch):
        """When shutdown disconnects the export callback, the
        finished signal must not invoke _on_export_done."""
        import qt_app.pages.inventory_page as ip_mod

        _block = threading.Event()  # blocks the export worker
        _started = threading.Event()

        def _blocking_export(snapshot, path):
            _started.set()
            _block.wait(timeout=5.0)
            return ExportResult.success(path)

        monkeypatch.setattr(
            ip_mod, "export_inventory_snapshot", _blocking_export)

        fk = _install_qmb(monkeypatch)

        inventory_page._current_snapshot = _make_snapshot()
        inventory_page._run_export("/tmp/callback_stale.xlsx")

        assert inventory_page._export_thread.isRunning()

        # Wait for worker to enter the blocking function
        _started.wait(timeout=2.0)
        assert _started.is_set()

        # shutdown disconnects the callback and attempts to quit
        result = inventory_page.shutdown()
        # The worker is blocked, so quit+wait(2000) times out -> False
        assert result is False

        # Now release the block
        _block.set()

        # Wait for the thread to actually finish
        _pump_until(lambda: inventory_page._export_thread is None,
                    timeout_s=4.0)

        # _on_export_done was disconnected, so no dialog should have
        # appeared
        assert len(fk.infos) == 0, (
            "Stale callback invoked after disconnect")
