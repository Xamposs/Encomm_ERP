"""Regression tests for ProductImportPreviewDialog — runs under pytest, exit 0."""

import pytest
from PySide6.QtWidgets import QApplication


def _fake_sig():
    from infrastructure.product_import_identity import ImportSourceSignature
    return ImportSourceSignature(
        file_size_bytes=100, file_sha256="a" * 64, mapping_sha256="b" * 64)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def dialog_factory(request, qapp):
    """Factory that records dialogs and cleans them up on teardown."""
    dialogs = []

    def _make(**kw):
        from qt_app.dialogs.product_import_preview_dialog import (
            ProductImportPreviewDialog)
        dlg = ProductImportPreviewDialog(**kw)
        dialogs.append(dlg)
        return dlg

    yield _make

    for dlg in dialogs:
        dlg.close()
        dlg.deleteLater()


class TestBusyLifecycle:

    def test_idle_by_default(self, dialog_factory):
        d = dialog_factory()
        assert not d.is_busy()

    def test_busy_when_thread_ref_exists(self, dialog_factory):
        d = dialog_factory()
        d._thread = object()
        assert d.is_busy()
        d._thread = None

    def test_start_worker_refuses_when_busy(self, dialog_factory):
        d = dialog_factory()
        d._thread = object()
        d._start_worker(object(), lambda x: None)
        assert d._worker is None
        assert d._thread is not None
        d._thread = None


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

    def test_workbook_a_populates_sheets(self, dialog_factory):
        d = dialog_factory()
        self._setup(d, "/a.xlsx", 1)
        r = self._r(20, 1, "/a.xlsx", ("Sheet1", "Sheet2"),
                     ("ColA", "ColB", "ColC", "ColD", "ColE"))
        d._on_inspect_done(r)
        assert d._sheet_combo.count() == 2
        assert d._sheet_combo.itemText(0) == "Sheet1"
        assert d._map_combos["barcode"].count() > 1

    def test_workbook_b_replaces_sheets(self, dialog_factory):
        d = dialog_factory()
        self._setup(d, "/a.xlsx", 1)
        d._on_inspect_done(
            self._r(20, 1, "/a.xlsx", ("Sheet1", "Sheet2"),
                    ("ColA", "ColB", "ColC", "ColD", "ColE")))
        d._file_path = "/b.xlsx"
        d._file_gen = 2
        d._current_file_path = "/b.xlsx"
        d._inspect_token = 21
        r2 = self._r(21, 2, "/b.xlsx", ("Data",),
                      ("X", "Y", "Z", "W", "Q"), sheet_name="Data")
        d._on_inspect_done(r2)
        assert d._sheet_combo.count() == 1
        assert d._sheet_combo.itemText(0) == "Data"

    def test_sheet_switch_preserves_list(self, dialog_factory):
        d = dialog_factory()
        self._setup(d, "/b.xlsx", 2)
        d._last_file_gen = 2
        d._sheet_combo.clear()
        d._sheet_combo.addItems(["Data", "Extra"])
        d._inspect_token = 22
        r = self._r(22, 2, "/b.xlsx", ("Data", "Extra"),
                     ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"),
                     sheet_name="Extra")
        d._on_inspect_done(r)
        assert d._sheet_combo.count() == 2

    def test_wrong_token_rejected(self, dialog_factory):
        d = dialog_factory()
        self._setup(d, "/f.xlsx", 1)
        d._sheet_combo.clear()
        d._sheet_combo.addItems(["RealSheet"])
        d._on_inspect_done(self._r(3, 1, "/f.xlsx", ("Bad",)))
        assert d._sheet_combo.itemText(0) == "RealSheet"

    def test_wrong_file_path_rejected(self, dialog_factory):
        d = dialog_factory()
        self._setup(d, "/real.xlsx", 1)
        d._sheet_combo.clear()
        d._sheet_combo.addItems(["Real"])
        d._on_inspect_done(self._r(20, 1, "/other.xlsx", ("Bad",)))
        assert d._sheet_combo.itemText(0) == "Real"


class TestPreviewUIReset:

    def test_hide_results_clears_rows(self, dialog_factory):
        d = dialog_factory()
        d._sample_table.setRowCount(3)
        d._error_table.setRowCount(5)
        d._hide_results()
        assert d._sample_table.rowCount() == 0
        assert d._error_table.rowCount() == 0

    def test_completed_preview_restores_ui(self, dialog_factory):
        d = dialog_factory()
        d._preview_btn.hide()
        d._cancel_btn.show()
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        d._closing = False
        d._on_thread_done()
        assert d._preview_btn.isEnabled()
        assert not d._preview_btn.isHidden()
        assert d._cancel_btn.isHidden()
        assert not d.is_busy()


class TestConflictUI:

    def test_conflict_btn_disabled_without_db(self, dialog_factory):
        d = dialog_factory(db_path="")
        assert not d._conflict_btn.isEnabled()

    def test_conflict_btn_enabled_with_db_and_file(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        d._set_controls_enabled(True)
        assert d._conflict_btn.isEnabled()

    def test_conflict_clears_on_new_file(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        d._conflict_summary_lbl.setText("old")
        d._conflict_summary_lbl.show()
        d._conflict_table.setRowCount(3)
        d._conflict_table.show()
        d._file_path = "/new.xlsx"
        d._file_gen += 1
        d._current_file_path = "/new.xlsx"
        d._sheet_combo.clear()
        d._hide_results()
        d._status_lbl.setText("")
        assert d._conflict_summary_lbl.isHidden()
        assert d._conflict_table.isHidden()
        assert d._conflict_table.rowCount() == 0

    def test_conflict_result_renders_summary(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult, ConflictRecord)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.success(
            "f.xlsx", "S1", 10, 10, 0, 0, 10, 5, 3, 2,
            [ConflictRecord("A", ("Name", "Price")),
             ConflictRecord("B", ("Stock",))],
            [], [], signature=_fake_sig())
        d._on_conflict_done(result)
        assert "Νέα προϊόντα: 5" in d._conflict_summary_lbl.text()
        assert "Όνομα, Τιμή" in d._conflict_table.item(0, 1).text()
        assert d._conflict_table.rowCount() == 2

    def test_cancelled_conflict_result_partial(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.cancelled(
            "f.xlsx", "S1", 500, 500, 0, 0, 300, 200, 70, 30, [], [], [])
        d._on_conflict_done(result)
        assert "μερική" in d._status_lbl.text()
        assert "Ταξινομήθηκαν: 300" in d._conflict_summary_lbl.text().replace("\n", " ")

    def test_conflict_btn_restored_after_cleanup(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        d._closing = False
        d._on_thread_done()
        assert not d._conflict_btn.isHidden()
        assert d._conflict_btn.isEnabled()

    def test_mapping_change_clears_conflict(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        d._conflict_summary_lbl.setText("stale")
        d._conflict_summary_lbl.show()
        d._conflict_table.setRowCount(3)
        d._conflict_table.show()
        d._on_mapping_changed("NewCol")
        assert d._conflict_summary_lbl.isHidden()
        assert d._conflict_table.isHidden()
        assert d._conflict_table.rowCount() == 0

    def test_conflict_refuses_when_busy(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        d._thread = object()
        d._on_run_conflict()
        assert d._worker is None

    def test_conflict_capped_at_50(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult, ConflictRecord)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        samples = tuple(
            ConflictRecord(f"B{i}", ("Name",)) for i in range(80))
        result = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 0, 0, 10, samples, [], [],
            signature=_fake_sig())
        d._on_conflict_done(result)
        assert d._conflict_table.rowCount() == 50


class TestPlanUI:

    def test_plan_group_visible_btn_disabled_before(self, dialog_factory):
        d = dialog_factory(db_path="/tmp/test.db")
        assert not d._plan_btn.isEnabled()
        assert not d._plan_grp.isHidden()

    def test_plan_btn_enabled_after_success(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=_fake_sig())
        d._on_conflict_done(result)
        assert d._plan_btn.isEnabled()

    def test_plan_btn_disabled_after_cancelled(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.cancelled(
            "f", "S1", 10, 10, 0, 0, 5, 2, 2, 1, [], [], [])
        d._on_conflict_done(result)
        assert not d._plan_btn.isEnabled()

    def test_build_plan_greek_summary(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=_fake_sig())
        d._on_conflict_done(result)
        d._on_build_plan()
        txt = d._plan_summary_lbl.text()
        assert "Προς μελλοντική προσθήκη: 4" in txt
        assert "Αλλαγές που απαιτούν έλεγχο: 3" in txt
        assert "Αλλαγές που θα παραλειφθούν: 0" in txt
        assert "Ταυτότητα αρχείου (SHA-256):" in txt

    def test_skip_policy_skipped_changed(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=_fake_sig())
        d._on_conflict_done(result)
        d._plan_policy.setCurrentIndex(1)
        d._on_build_plan()
        txt = d._plan_summary_lbl.text()
        assert "Αλλαγές που απαιτούν έλεγχο: 0" in txt
        assert "Αλλαγές που θα παραλειφθούν: 3" in txt

    def test_plan_cleared_via_hide_results(self, dialog_factory):
        from infrastructure.product_import_conflicts import (
            ImportConflictResult)
        d = dialog_factory(db_path="/tmp/test.db")
        d._file_path = "f.xlsx"
        d._sheet_combo.addItem("S1")
        d._sheet_combo.setCurrentIndex(0)
        result = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=_fake_sig())
        d._on_conflict_done(result)
        d._on_build_plan()
        assert d._plan_btn.isEnabled()
        d._hide_results()
        assert not d._plan_btn.isEnabled()
        assert d._last_conflict_result is None
