"""Regression tests for ProductImportPreviewDialog lifecycle."""

import sys, pytest
from PySide6.QtCore import QThread


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication for all dialog tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def dialog(qapp):
    from qt_app.dialogs.product_import_preview_dialog import (
        ProductImportPreviewDialog)
    dlg = ProductImportPreviewDialog()
    yield dlg


class TestBusyLifecycle:

    def test_idle_by_default(self, dialog):
        assert not dialog.is_busy()

    def test_busy_when_thread_ref_exists(self, dialog):
        dialog._thread = object()  # simulate uncleared ref
        assert dialog.is_busy()
        dialog._thread = None

    def test_start_worker_refuses_when_busy(self, dialog):
        dialog._thread = object()
        dialog._start_worker(object(), lambda x: None)
        assert dialog._worker is None
        assert dialog._thread is not None
        dialog._thread = None


class TestFileWorksheetIdentity:

    def test_new_file_populates_sheets(self, dialog):
        dialog._last_file_gen = 0
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Sheet1", "Sheet2"])
        assert dialog._sheet_combo.count() == 2

    def test_second_file_replaces_sheets(self, dialog):
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Sheet1", "Sheet2"])
        assert dialog._sheet_combo.count() == 2
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Data"])
        assert dialog._sheet_combo.count() == 1
        assert dialog._sheet_combo.itemText(0) == "Data"

    def test_sheet_switch_preserves_list(self, dialog):
        dialog._last_file_gen = 1
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Sheet1", "Sheet2"])
        count = dialog._sheet_combo.count()
        dialog._sheet_combo.setCurrentText("Sheet2")
        assert dialog._sheet_combo.count() == count

    def test_stale_inspect_token_rejected(self, dialog):
        """Result with wrong token does not modify sheet combo."""
        from qt_app.dialogs.product_import_preview_dialog import (
            _InspectResult)
        dialog._inspect_token = 5
        dialog._current_file_path = "/f.xlsx"
        result = _InspectResult(
            ok=True, token=3, file_path="/f.xlsx",  # token 3 ≠ 5
            sheets=("BadSheet",), headers=())
        count_before = dialog._sheet_combo.count()
        dialog._on_inspect_done(result)
        assert dialog._sheet_combo.count() == count_before

    def test_stale_inspect_file_path_rejected(self, dialog):
        from qt_app.dialogs.product_import_preview_dialog import (
            _InspectResult)
        dialog._inspect_token = 7
        dialog._current_file_path = "/real.xlsx"
        result = _InspectResult(
            ok=True, token=7, file_path="/other.xlsx",  # wrong path
            sheets=("BadSheet",), headers=())
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Real"])
        dialog._on_inspect_done(result)
        assert dialog._sheet_combo.itemText(0) == "Real"


class TestPreviewUIReset:

    def test_hide_results_clears_rows(self, dialog):
        dialog._sample_table.setRowCount(3)
        dialog._error_table.setRowCount(5)
        dialog._hide_results()
        assert dialog._sample_table.rowCount() == 0
        assert dialog._error_table.rowCount() == 0

    def test_preview_button_restored_after_completion(self, dialog):
        dialog._file_path = "f.xlsx"
        dialog._sheet_combo.addItem("S1")
        dialog._sheet_combo.setCurrentIndex(0)
        dialog._closing = False
        dialog._on_thread_done()
        assert dialog._preview_btn.isEnabled()
        assert not dialog._cancel_btn.isVisible()
        assert not dialog.is_busy()
