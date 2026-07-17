"""Regression tests for ProductImportPreviewDialog — runs under pytest."""

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def dialog(qapp):
    from qt_app.dialogs.product_import_preview_dialog import (
        ProductImportPreviewDialog)
    dlg = ProductImportPreviewDialog()
    yield dlg
    dlg.close()
    dlg.deleteLater()
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()


class TestBusyLifecycle:

    def test_idle_by_default(self, dialog):
        assert not dialog.is_busy()

    def test_busy_when_thread_ref_exists(self, dialog):
        dialog._thread = object()
        assert dialog.is_busy()
        dialog._thread = None

    def test_start_worker_refuses_when_busy(self, dialog):
        dialog._thread = object()
        dialog._start_worker(object(), lambda x: None)
        assert dialog._worker is None
        assert dialog._thread is not None
        dialog._thread = None


class TestFileWorksheetIdentity:

    @staticmethod
    def _r(token, file_gen, file_path, sheets, headers=(),
           sheet_name="", suggested=None):
        from qt_app.dialogs.product_import_preview_dialog import (
            _InspectResult)
        return _InspectResult(
            ok=True, token=token, file_gen=file_gen, file_path=file_path,
            sheet_name=sheet_name or (sheets[0] if sheets else ""),
            sheets=sheets, headers=headers, suggested_mapping=suggested)

    @staticmethod
    def _setup(d, path, gen):
        d._file_path = path
        d._file_gen = gen
        d._current_file_path = path
        d._inspect_token = 20
        d._last_file_gen = 0

    def test_workbook_a_populates_sheets(self, dialog):
        self._setup(dialog, "/a.xlsx", 1)
        r = self._r(20, 1, "/a.xlsx", ("Sheet1", "Sheet2"),
                     ("ColA", "ColB", "ColC", "ColD", "ColE"))
        dialog._on_inspect_done(r)
        assert dialog._sheet_combo.count() == 2
        assert dialog._sheet_combo.itemText(0) == "Sheet1"
        assert dialog._map_combos["barcode"].count() > 1

    def test_workbook_b_replaces_sheets(self, dialog):
        self._setup(dialog, "/a.xlsx", 1)
        dialog._on_inspect_done(
            self._r(20, 1, "/a.xlsx", ("Sheet1", "Sheet2"),
                    ("ColA", "ColB", "ColC", "ColD", "ColE")))
        dialog._file_path = "/b.xlsx"
        dialog._file_gen = 2
        dialog._current_file_path = "/b.xlsx"
        dialog._inspect_token = 21
        r2 = self._r(21, 2, "/b.xlsx", ("Data",),
                      ("X", "Y", "Z", "W", "Q"), sheet_name="Data")
        dialog._on_inspect_done(r2)
        assert dialog._sheet_combo.count() == 1
        assert dialog._sheet_combo.itemText(0) == "Data"

    def test_sheet_switch_preserves_list(self, dialog):
        self._setup(dialog, "/b.xlsx", 2)
        dialog._last_file_gen = 2
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Data", "Extra"])
        dialog._inspect_token = 22
        r = self._r(22, 2, "/b.xlsx", ("Data", "Extra"),
                     ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"),
                     sheet_name="Extra")
        dialog._on_inspect_done(r)
        assert dialog._sheet_combo.count() == 2

    def test_wrong_token_rejected(self, dialog):
        self._setup(dialog, "/f.xlsx", 1)
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["RealSheet"])
        dialog._on_inspect_done(
            self._r(3, 1, "/f.xlsx", ("Bad",)))
        assert dialog._sheet_combo.itemText(0) == "RealSheet"

    def test_wrong_file_path_rejected(self, dialog):
        self._setup(dialog, "/real.xlsx", 1)
        dialog._sheet_combo.clear()
        dialog._sheet_combo.addItems(["Real"])
        dialog._on_inspect_done(
            self._r(20, 1, "/other.xlsx", ("Bad",)))
        assert dialog._sheet_combo.itemText(0) == "Real"


class TestPreviewUIReset:

    def test_hide_results_clears_rows(self, dialog):
        dialog._sample_table.setRowCount(3)
        dialog._error_table.setRowCount(5)
        dialog._hide_results()
        assert dialog._sample_table.rowCount() == 0
        assert dialog._error_table.rowCount() == 0

    def test_completed_preview_restores_ui(self, dialog):
        dialog._preview_btn.hide()
        dialog._cancel_btn.show()
        dialog._file_path = "f.xlsx"
        dialog._sheet_combo.addItem("S1")
        dialog._sheet_combo.setCurrentIndex(0)
        dialog._closing = False
        dialog._on_thread_done()
        assert dialog._preview_btn.isEnabled()
        assert not dialog._preview_btn.isHidden()
        assert dialog._cancel_btn.isHidden()
        assert not dialog.is_busy()
