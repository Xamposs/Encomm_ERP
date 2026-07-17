"""Preview dialog for XLSX product import — read-only, no database writes."""

from __future__ import annotations

import os, threading
from dataclasses import dataclass
from typing import Tuple

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QGroupBox, QProgressBar, QDialogButtonBox,
    QCheckBox,
)

from qt_app import styles
from infrastructure.product_import_preview import (
    preview_product_import_xlsx, ImportColumnMapping,
    suggest_mapping, list_xlsx_sheets, inspect_xlsx_headers,
    ProductImportPreview,
)


FIELD_KEYS = ("barcode", "name", "stock", "price", "expiry")
FIELD_LABELS = {
    "barcode": "Barcode", "name": "Όνομα Προϊόντος",
    "stock": "Απόθεμα", "price": "Τιμή", "expiry": "Ημ. Λήξης",
}


@dataclass
class _InspectResult:
    ok: bool
    token: int = 0
    file_gen: int = 0
    file_path: str = ""
    sheet_name: str = ""
    error: str = ""
    sheets: Tuple[str, ...] = ()
    headers: Tuple[str, ...] = ()
    suggested_mapping: ImportColumnMapping | None = None


class _InspectWorker(QObject):
    finished = Signal(_InspectResult)

    def __init__(self, token, file_gen, file_path, sheet_name=None, parent=None):
        super().__init__(parent)
        self._token, self._file_gen, self._fp, self._sn = (
            token, file_gen, file_path, sheet_name)

    def run(self):
        try:
            sheets = list_xlsx_sheets(self._fp)
            sn = self._sn or (sheets[0] if sheets else "")
            headers = inspect_xlsx_headers(self._fp, sn) if sn else ()
            suggested = suggest_mapping(self._fp, sn) if sn else None
            self.finished.emit(_InspectResult(
                ok=True, token=self._token, file_gen=self._file_gen,
                file_path=self._fp,
                sheet_name=sn, sheets=sheets, headers=headers,
                suggested_mapping=suggested))
        except Exception as e:
            self.finished.emit(_InspectResult(
                ok=False, token=self._token, error=str(e)))


class _PreviewWorker(QObject):
    finished = Signal(ProductImportPreview)

    def __init__(self, file_path, mapping, sheet_name, cancel_event, parent=None):
        super().__init__(parent)
        self._fp, self._m, self._sn, self._cancel = (
            file_path, mapping, sheet_name, cancel_event)

    def run(self):
        self.finished.emit(preview_product_import_xlsx(
            self._fp, self._m, self._sn, cancel_event=self._cancel))


class _ConflictWorker(QObject):
    finished = Signal(object)  # ImportConflictResult

    def __init__(self, file_path, mapping, db_path, sheet_name,
                 cancel_event, parent=None):
        super().__init__(parent)
        self._fp = file_path
        self._m = mapping
        self._db = db_path
        self._sn = sheet_name
        self._cancel = cancel_event

    def run(self):
        from infrastructure.product_import_conflicts import (
            analyze_import_conflicts)
        self.finished.emit(analyze_import_conflicts(
            self._fp, self._m, self._db, self._sn,
            cancel_event=self._cancel))


class _CommitWorker(QObject):
    finished = Signal(object)  # ImportCommitResult

    def __init__(self, file_path, mapping, plan, db_path, cancel_event,
                 parent=None):
        super().__init__(parent)
        self._fp, self._m, self._plan, self._db, self._cancel = (
            file_path, mapping, plan, db_path, cancel_event)

    def run(self):
        from infrastructure.product_import_commit import (
            commit_new_products_from_xlsx)
        self.finished.emit(commit_new_products_from_xlsx(
            self._fp, self._m, self._plan, self._db,
            cancel_event=self._cancel))


class ProductImportPreviewDialog(QDialog):
    shutdown_ready = Signal()
    import_completed = Signal(int)

    def __init__(self, parent=None, db_path=""):
        super().__init__(parent)
        self.setWindowTitle("Προεπισκόπηση Εισαγωγής Excel")
        self.setMinimumSize(750, 600)
        self._db_path = db_path
        self._file_path: str = ""
        self._sheet_name: str = ""
        self._cancel_event: threading.Event | None = None
        self._worker: QObject | None = None
        self._thread: QThread | None = None
        self._closing = False
        self._inspect_token = 0
        self._file_gen = 0
        self._last_file_gen = 0
        self._current_file_path = ""
        self._last_conflict_result = None  # for plan building
        self._current_plan = None  # ImportPlan
        self._operation = ""  # "preview", "conflict", "commit"
        self._build_ui()
        self._reset_state()

    # ── Public shutdown contract ──────────────────────────────────────
    def is_busy(self) -> bool:
        return self._thread is not None

    def request_shutdown(self) -> None:
        if not self.is_busy():
            super().reject()
            return
        if self._cancel_event:
            self._cancel_event.set()
        self._closing = True
        self._set_controls_enabled(False)
        self._status_lbl.setText("Ακύρωση προεπισκόπησης…")
        self._status_lbl.setStyleSheet(f"color: {styles.AMBER}; font-size: 12px;")

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # File
        grp = QGroupBox("1. Επιλογή αρχείου")
        fl = QHBoxLayout()
        self._file_lbl = QLabel("Δεν επιλέχθηκε αρχείο")
        self._file_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED};")
        fl.addWidget(self._file_lbl, 1)
        self._file_btn = QPushButton("📂  Επιλογή αρχείου .xlsx")
        self._file_btn.setStyleSheet(self._btn_qss())
        self._file_btn.clicked.connect(self._on_choose_file)
        fl.addWidget(self._file_btn)
        grp.setLayout(fl)
        lay.addWidget(grp)

        # Sheet
        grp2 = QGroupBox("2. Φύλλο εργασίας")
        sl = QHBoxLayout()
        self._sheet_combo = QComboBox()
        self._sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
        sl.addWidget(self._sheet_combo)
        grp2.setLayout(sl)
        lay.addWidget(grp2)

        # Mapping
        grp3 = QGroupBox("3. Αντιστοίχιση στηλών")
        ml = QVBoxLayout()
        self._map_combos: dict[str, QComboBox] = {}
        for key in FIELD_KEYS:
            row = QHBoxLayout()
            lbl = QLabel(FIELD_LABELS[key] + ":")
            lbl.setMinimumWidth(130)
            row.addWidget(lbl)
            cb = QComboBox()
            cb.setMinimumWidth(200)
            cb.currentTextChanged.connect(self._on_mapping_changed)
            self._map_combos[key] = cb
            row.addWidget(cb, 1)
            row.addStretch()
            ml.addLayout(row)
        grp3.setLayout(ml)
        lay.addWidget(grp3)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        lay.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        lay.addWidget(self._status_lbl)

        # Action buttons
        btns_row = QHBoxLayout()
        self._preview_btn = QPushButton("🔍  Εκτέλεση Προεπισκόπησης")
        self._preview_btn.setStyleSheet(self._btn_qss())
        self._preview_btn.clicked.connect(self._on_run_preview)
        self._preview_btn.setEnabled(False)
        btns_row.addWidget(self._preview_btn)
        self._cancel_btn = QPushButton("✕  Ακύρωση")
        self._cancel_btn.setStyleSheet(self._btn_qss())
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        btns_row.addWidget(self._cancel_btn)
        self._conflict_btn = QPushButton("🔎  Ανάλυση συγκρούσεων βάσης")
        self._conflict_btn.setStyleSheet(self._btn_qss())
        self._conflict_btn.clicked.connect(self._on_run_conflict)
        self._conflict_btn.setEnabled(False)
        if not self._db_path:
            self._conflict_btn.setToolTip(
                "Απαιτείται διαδρομή βάσης δεδομένων.")
        btns_row.addWidget(self._conflict_btn)
        btns_row.addStretch()
        lay.addLayout(btns_row)

        # Results
        rs = QVBoxLayout()
        self._sample_lbl = QLabel("")
        self._sample_lbl.setStyleSheet(f"color: {styles.ACCENT}; font-weight: bold;")
        rs.addWidget(self._sample_lbl)
        self._sample_table = QTableWidget(0, 5)
        self._sample_table.setHorizontalHeaderLabels(
            ["Barcode", "Όνομα", "Στοκ", "Τιμή", "Ημ. Λήξης"])
        self._sample_table.horizontalHeader().setStretchLastSection(True)
        self._sample_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._sample_table.hide()
        rs.addWidget(self._sample_table)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {styles.RED}; font-weight: bold;")
        rs.addWidget(self._error_lbl)
        self._error_table = QTableWidget(0, 4)
        self._error_table.setHorizontalHeaderLabels(
            ["Γραμμή", "Barcode", "Κωδικός", "Μήνυμα"])
        self._error_table.horizontalHeader().setStretchLastSection(True)
        self._error_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._error_table.setMaximumHeight(200)
        self._error_table.hide()
        rs.addWidget(self._error_table)

        self._no_write_lbl = QLabel(
            "Δεν έγιναν αλλαγές στην αποθήκη. "
            "Η προεπισκόπηση είναι μόνο για έλεγχο.")
        self._no_write_lbl.setWordWrap(True)
        self._no_write_lbl.setStyleSheet(f"color: {styles.AMBER}; font-size: 12px;")
        self._no_write_lbl.hide()
        rs.addWidget(self._no_write_lbl)

        # Conflict analysis results
        self._conflict_summary_lbl = QLabel("")
        self._conflict_summary_lbl.setWordWrap(True)
        self._conflict_summary_lbl.setStyleSheet(
            f"color: {styles.ACCENT}; font-size: 12px;")
        self._conflict_summary_lbl.hide()
        rs.addWidget(self._conflict_summary_lbl)
        self._conflict_table = QTableWidget(0, 2)
        self._conflict_table.setHorizontalHeaderLabels(
            ["Barcode", "Αλλαγμένα Πεδία"])
        self._conflict_table.horizontalHeader().setStretchLastSection(True)
        self._conflict_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._conflict_table.setMaximumHeight(200)
        self._conflict_table.hide()
        rs.addWidget(self._conflict_table)

        # C2: read-only detail table — current vs incoming values
        self._detail_notice_lbl = QLabel(
            "⚠ Τα παρακάτω προϊόντα έχουν διαφορές αλλά "
            "ΔΕΝ θα αλλάξουν από αυτή την εισαγωγή. "
            "Εμφανίζονται μόνο για έλεγχο.")
        self._detail_notice_lbl.setWordWrap(True)
        self._detail_notice_lbl.setStyleSheet(
            f"color: {styles.AMBER}; font-size: 12px; font-weight: bold;")
        self._detail_notice_lbl.hide()
        rs.addWidget(self._detail_notice_lbl)

        self._detail_truncated_lbl = QLabel("")
        self._detail_truncated_lbl.setWordWrap(True)
        self._detail_truncated_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 11px;")
        self._detail_truncated_lbl.hide()
        rs.addWidget(self._detail_truncated_lbl)

        self._detail_table = QTableWidget(0, 4)
        self._detail_table.setHorizontalHeaderLabels(
            ["Barcode", "Πεδίο", "Τρέχουσα Τιμή", "Νέα Τιμή (Excel)"])
        self._detail_table.horizontalHeader().setStretchLastSection(True)
        self._detail_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._detail_table.setMaximumHeight(250)
        self._detail_table.hide()
        rs.addWidget(self._detail_table)

        # Import plan section
        self._plan_grp = QGroupBox("4. Σχέδιο εισαγωγής (χωρίς αλλαγές)")
        pl = QVBoxLayout()
        pol_row = QHBoxLayout()
        pol_row.addWidget(QLabel("Πολιτική για υπάρχουσες αλλαγές:"))
        self._plan_policy = QComboBox()
        self._plan_policy.addItem(
            "Απαιτείται χειροκίνητος έλεγχος (προτεινόμενο)")
        self._plan_policy.addItem("Παράλειψη αλλαγών")
        pol_row.addWidget(self._plan_policy, 1)
        pl.addLayout(pol_row)
        self._plan_btn = QPushButton("📋  Δημιουργία σχεδίου εισαγωγής")
        self._plan_btn.setStyleSheet(self._btn_qss())
        self._plan_btn.clicked.connect(self._on_build_plan)
        self._plan_btn.setEnabled(False)
        pl.addWidget(self._plan_btn)
        self._plan_summary_lbl = QLabel("")
        self._plan_summary_lbl.setWordWrap(True)
        self._plan_summary_lbl.setStyleSheet(
            f"color: {styles.ACCENT}; font-size: 12px;")
        self._plan_summary_lbl.hide()
        pl.addWidget(self._plan_summary_lbl)
        self._commit_check = QCheckBox(
            "Κατανοώ ότι θα προστεθούν μόνο νέα προϊόντα. "
            "Τα υπάρχοντα δεν θα αλλάξουν.")
        self._commit_check.hide()
        self._commit_check.toggled.connect(self._on_commit_check_toggled)
        pl.addWidget(self._commit_check)
        self._commit_btn = QPushButton(
            "✅  Εκτέλεση ασφαλούς εισαγωγής νέων προϊόντων")
        self._commit_btn.setStyleSheet(self._btn_qss())
        self._commit_btn.clicked.connect(self._on_run_commit)
        self._commit_btn.setEnabled(False)
        self._commit_btn.hide()
        pl.addWidget(self._commit_btn)
        self._plan_grp.setLayout(pl)
        self._plan_grp.show()
        rs.addWidget(self._plan_grp)
        lay.addLayout(rs)

        dbb = QDialogButtonBox(QDialogButtonBox.Close)
        dbb.rejected.connect(self.reject)
        lay.addWidget(dbb)

    @staticmethod
    def _btn_qss():
        return (
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; font-size: 13px; "
            f"font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    # ── Controls gating ───────────────────────────────────────────────
    def _set_controls_enabled(self, enabled: bool):
        self._file_btn.setEnabled(enabled)
        self._sheet_combo.setEnabled(enabled)
        for cb in self._map_combos.values():
            cb.setEnabled(enabled)
        if enabled:
            self._preview_btn.show()
            self._preview_btn.setEnabled(
                bool(self._file_path)
                and len(self._sheet_combo.currentText()) > 0)
            self._conflict_btn.show()
            self._conflict_btn.setEnabled(
                bool(self._db_path) and bool(self._file_path)
                and len(self._sheet_combo.currentText()) > 0)
            self._cancel_btn.hide()
        else:
            self._preview_btn.setEnabled(False)
            self._conflict_btn.setEnabled(False)
            if not self.is_busy():
                self._preview_btn.hide()
                self._conflict_btn.hide()
                self._cancel_btn.hide()

    # ── File / sheet ──────────────────────────────────────────────────
    def _on_choose_file(self):
        if self.is_busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Επιλογή αρχείου Excel", "", "Excel Files (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            QMessageBox.warning(self, "Σφάλμα",
                                "Το αρχείο πρέπει να έχει κατάληξη .xlsx.")
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Σφάλμα", "Το αρχείο δεν βρέθηκε.")
            return
        # Clear everything from previous file
        self._file_path = path
        self._file_gen += 1
        self._current_file_path = path
        self._file_lbl.setText(os.path.basename(path))
        self._sheet_combo.clear()
        self._hide_results()
        self._status_lbl.setText("")
        self._inspect_file()  # sheet_name=None → auto-pick first

    def _inspect_file(self, sheet_name=None):
        if self.is_busy():
            return
        self._inspect_token += 1
        self._set_controls_enabled(False)
        self._status_lbl.setText("🔄 Ανάγνωση δομής αρχείου…")
        self._start_worker(
            _InspectWorker(self._inspect_token, self._file_gen,
                           self._file_path, sheet_name),
            self._on_inspect_done)

    def _on_inspect_done(self, result: _InspectResult):
        if (result.token != self._inspect_token
                or result.file_path != self._current_file_path):
            return
        if not result.ok:
            QMessageBox.warning(self, "Σφάλμα",
                                f"Αδυναμία ανάγνωσης: {result.error}")
            return
        # Repopulate sheet combo only on new file (file_gen increased)
        if result.file_gen > self._last_file_gen:
            self._last_file_gen = result.file_gen
            self._sheet_combo.blockSignals(True)
            self._sheet_combo.clear()
            self._sheet_combo.addItems(result.sheets)
            self._sheet_combo.blockSignals(False)
        if result.sheet_name:
            self._sheet_name = result.sheet_name
            idx = self._sheet_combo.findText(result.sheet_name)
            if idx >= 0:
                self._sheet_combo.blockSignals(True)
                self._sheet_combo.setCurrentIndex(idx)
                self._sheet_combo.blockSignals(False)
        self._apply_headers(result.headers, result.suggested_mapping)

    def _on_sheet_changed(self, name):
        if not name or not self._file_path or self.is_busy():
            return
        self._hide_results()
        self._sheet_name = name
        self._inspect_file(sheet_name=name)

    def _on_mapping_changed(self, _text):
        """Clear stale results when user changes column mapping."""
        if not self.is_busy():
            self._hide_results()
            self._status_lbl.setText("")

    def _apply_headers(self, headers, suggested):
        for key in FIELD_KEYS:
            cb = self._map_combos[key]
            cb.clear()
            cb.addItem("— Αυτόματο —", "")
            cb.addItems(headers)
            if suggested:
                col = getattr(suggested, f"{key}_column", None)
                if col and col in headers:
                    cb.setCurrentText(col)

    # ── Run preview ───────────────────────────────────────────────────
    def _on_run_preview(self):
        if self.is_busy():
            return
        mapping = self._get_mapping()
        if mapping is None:
            QMessageBox.warning(self, "Σφάλμα",
                "Επιλέξτε μοναδικές στήλες για κάθε πεδίο.")
            return
        self._hide_results()
        self._set_controls_enabled(False)
        self._preview_btn.hide()
        self._cancel_btn.show()
        self._progress.show()
        self._status_lbl.setText("🔄 Εκτέλεση προεπισκόπησης…")
        self._operation = "preview"
        self._cancel_event = threading.Event()
        self._start_worker(
            _PreviewWorker(self._file_path, mapping, self._sheet_name,
                           self._cancel_event),
            self._on_preview_done)

    def _on_cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self._cancel_btn.setEnabled(False)
        if self._operation == "commit":
            self._status_lbl.setText("Ακύρωση εισαγωγής…")
        elif self._operation == "conflict":
            self._status_lbl.setText("Ακύρωση ανάλυσης…")
        else:
            self._status_lbl.setText("Ακύρωση προεπισκόπησης…")

    # ── Conflict analysis ─────────────────────────────────────────────
    def _on_run_conflict(self):
        if self.is_busy() or not self._db_path:
            return
        mapping = self._get_mapping()
        if mapping is None:
            QMessageBox.warning(self, "Σφάλμα",
                "Επιλέξτε μοναδικές στήλες για κάθε πεδίο.")
            return
        self._hide_results()
        self._set_controls_enabled(False)
        self._preview_btn.hide()
        self._conflict_btn.hide()
        self._cancel_btn.show()
        self._progress.show()
        self._status_lbl.setText("🔄 Ανάλυση συγκρούσεων βάσης…")
        self._operation = "conflict"
        self._cancel_event = threading.Event()
        self._start_worker(
            _ConflictWorker(self._file_path, mapping, self._db_path,
                            self._sheet_name, self._cancel_event),
            self._on_conflict_done)

    def _on_conflict_done(self, result):
        self._progress.hide()
        self._cancel_btn.hide()
        self._no_write_lbl.show()

        FIELD_GREEK = {"Name": "Όνομα", "Stock": "Απόθεμα",
                       "Price": "Τιμή", "ExpiryDate": "Ημ. λήξης"}

        if result.cancelled:
            self._status_lbl.setText(
                f"⚠ Ανάλυση ακυρώθηκε (μερική). "
                f"Ταξινομήθηκαν {result.classified_rows} γραμμές.")
            self._last_conflict_result = None
            self._plan_btn.setEnabled(False)
        elif not result.ok:
            self._status_lbl.setText(
                f"❌ {result.error_message or 'Ανάλυση απέτυχε.'}")
            self._last_conflict_result = None
            self._plan_btn.setEnabled(False)
        else:
            self._status_lbl.setText("✅ Ανάλυση συγκρούσεων ολοκληρώθηκε.")
            self._last_conflict_result = result
            self._plan_btn.setEnabled(
                result.source_signature is not None)

        summary = (
            f"Νέα προϊόντα: {result.new_barcodes}  ·  "
            f"Ήδη ίδια: {result.unchanged_existing}  ·  "
            f"Υπάρχουσες αλλαγές: {result.changed_existing}\n"
            f"Έγκυρα: {result.valid_rows}  ·  "
            f"Άκυρα: {result.invalid_rows}  ·  "
            f"Διπλότυπα: {result.duplicate_barcodes}  ·  "
            f"Ταξινομήθηκαν: {result.classified_rows}")
        self._conflict_summary_lbl.setText(summary)
        self._conflict_summary_lbl.show()

        if result.conflict_samples:
            display_count = min(len(result.conflict_samples), 50)
            self._conflict_table.setRowCount(display_count)
            for r in range(display_count):
                c = list(result.conflict_samples)[r]
                self._conflict_table.setItem(
                    r, 0, QTableWidgetItem(c.barcode))
                greek_fields = ", ".join(
                    FIELD_GREEK.get(f, f) for f in c.changed_fields)
                self._conflict_table.setItem(
                    r, 1, QTableWidgetItem(greek_fields))
            self._conflict_table.show()

        # C2: detail rows with current/incoming values
        details = getattr(result, 'conflict_details', ())
        FIELD_GREEK_REV = {
            "Name": "Όνομα", "Stock": "Απόθεμα",
            "Price": "Τιμή", "ExpiryDate": "Ημ. Λήξης"}
        if details:
            self._detail_notice_lbl.show()
            self._detail_table.setRowCount(len(details))
            for r, d in enumerate(details):
                self._detail_table.setItem(
                    r, 0, QTableWidgetItem(d.barcode))
                self._detail_table.setItem(
                    r, 1, QTableWidgetItem(
                        FIELD_GREEK_REV.get(d.field, d.field)))
                self._detail_table.setItem(
                    r, 2, QTableWidgetItem(d.current_value))
                self._detail_table.setItem(
                    r, 3, QTableWidgetItem(d.incoming_value))
            self._detail_table.show()
            # Show truncation notice if detail buffer is full
            _MAX_DETAILS = 200
            if len(details) >= _MAX_DETAILS:
                self._detail_truncated_lbl.setText(
                    f"⚠ Εμφανίζονται οι πρώτες {len(details)} διαφορές "
                    f"(όριο {_MAX_DETAILS}). "
                    f"Συνολικά υπάρχουν {result.changed_existing} προϊόντα "
                    f"με αλλαγές. Τα υπάρχοντα ΔΕΝ θα τροποποιηθούν.")
                self._detail_truncated_lbl.show()
        else:
            self._detail_notice_lbl.hide()
            self._detail_table.hide()
            self._detail_truncated_lbl.hide()

        # Also show preview errors/samples from the underlying scan
        if hasattr(result, 'sample_rows') and result.sample_rows:
            self._sample_lbl.setText(
                f"Δείγμα ({len(result.sample_rows)}):")
            self._sample_lbl.show()
            self._sample_table.setRowCount(len(result.sample_rows))
            for r, row in enumerate(result.sample_rows):
                for c, val in enumerate(row):
                    self._sample_table.setItem(r, c, QTableWidgetItem(str(val)))
            self._sample_table.show()
        if hasattr(result, 'errors') and result.errors:
            self._error_lbl.setText(f"Σφάλματα ({len(result.errors)}):")
            self._error_lbl.show()
            self._error_table.setRowCount(len(result.errors))
            for r, e in enumerate(result.errors):
                self._error_table.setItem(r, 0,
                    QTableWidgetItem(str(e.row_number)))
                self._error_table.setItem(r, 1, QTableWidgetItem(e.barcode))
                self._error_table.setItem(r, 2, QTableWidgetItem(e.code))
                self._error_table.setItem(r, 3, QTableWidgetItem(e.message))
            self._error_table.show()
        # Do NOT re-enable controls — _on_thread_done does it

    def _on_build_plan(self):
        if self._last_conflict_result is None:
            return
        from infrastructure.product_import_plan import (
            build_import_plan, ImportReviewPolicy, ChangedPolicy)
        policy = ImportReviewPolicy(
            changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW
            if self._plan_policy.currentIndex() == 0
            else ChangedPolicy.SKIP_CHANGES)
        try:
            plan = build_import_plan(self._last_conflict_result, policy)
        except ValueError as e:
            self._plan_summary_lbl.setText(f"❌ {e}")
            self._plan_summary_lbl.show()
            return
        summary = (
            f"📋 Σχέδιο εισαγωγής (ανάγνωση μόνο):\n"
            f"Προς μελλοντική προσθήκη: {plan.planned_new}\n"
            f"Ίδια προϊόντα που θα παραλειφθούν: {plan.skipped_identical}\n"
            f"Αλλαγές που απαιτούν έλεγχο: {plan.manual_review}\n"
            f"Αλλαγές που θα παραλειφθούν: {plan.skipped_changed}\n"
            f"Άκυρες γραμμές που απορρίπτονται: {plan.rejected_invalid}\n"
            f"Διπλότυπα που παραλείπονται: {plan.skipped_duplicates}\n"
            f"\nΤαυτότητα αρχείου (SHA-256): "
            f"{plan.source_signature.file_sha256[:12]}…\n"
            f"Το σχέδιο ισχύει μόνο για αυτό το αρχείο και αυτή "
            f"την αντιστοίχιση στηλών.")
        self._plan_summary_lbl.setText(summary)
        self._plan_summary_lbl.show()

        # Store plan and show commit controls
        self._current_plan = plan
        self._commit_check.show()
        self._commit_check.setChecked(False)
        self._commit_btn.show()
        self._commit_btn.setEnabled(False)

    def _on_commit_check_toggled(self, checked):
        self._commit_btn.setEnabled(
            checked and self._current_plan is not None
            and self._current_plan.planned_new > 0
            and not self.is_busy())

    def _on_run_commit(self):
        if self.is_busy() or self._current_plan is None:
            return
        plan = self._current_plan
        if plan.planned_new == 0:
            return
        # Confirmation
        reply = QMessageBox.question(
            self, "Επιβεβαίωση εισαγωγής",
            f"Θα προστεθούν {plan.planned_new} νέα προϊόντα.\n"
            f"Τα υπάρχοντα προϊόντα ΔΕΝ θα αλλάξουν.\n\n"
            f"Συνέχεια;",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        mapping = self._get_mapping()
        if mapping is None:
            return
        self._set_controls_enabled(False)
        self._preview_btn.hide()
        self._conflict_btn.hide()
        self._plan_btn.hide()
        self._commit_btn.hide()
        self._cancel_btn.show()
        self._progress.show()
        self._status_lbl.setText("🔄 Εκτέλεση ασφαλούς εισαγωγής…")
        self._operation = "commit"
        self._cancel_event = threading.Event()
        self._start_worker(
            _CommitWorker(self._file_path, mapping, plan,
                          self._db_path, self._cancel_event),
            self._on_commit_done)

    def _on_commit_done(self, result):
        self._progress.hide()
        self._cancel_btn.hide()
        self._operation = ""
        # Hide the old plan text (future addition wording)
        self._plan_summary_lbl.hide()
        if result.cancelled:
            self._status_lbl.setText(
                "⚠ Η εισαγωγή ακυρώθηκε. Δεν έγιναν αλλαγές.")
            self._no_write_lbl.show()
        elif not result.ok:
            self._status_lbl.setText(
                f"❌ Αποτυχία: {result.error_message}")
            self._no_write_lbl.show()
        else:
            self._status_lbl.setText(
                f"✅ Εισήχθησαν {result.inserted_rows} νέα προϊόντα. "
                f"({result.skipped_identical} ίδια παραλείφθηκαν, "
                f"{result.skipped_changed} υπάρχουσες αλλαγές "
                f"παραλείφθηκαν)")
            self._no_write_lbl.hide()
            self.import_completed.emit(result.inserted_rows)
        # Invalidate plan after any commit attempt
        self._current_plan = None
        self._commit_check.hide()
        self._commit_btn.hide()
        self._commit_btn.setEnabled(False)
        self._plan_btn.setEnabled(False)
        self._last_conflict_result = None

    def _get_mapping(self) -> ImportColumnMapping | None:
        cols = {}
        for key in FIELD_KEYS:
            val = self._map_combos[key].currentText()
            if not val or val == "— Αυτόματο —":
                return None
            cols[key] = val
        if len(set(cols.values())) != 5:
            return None
        return ImportColumnMapping(
            barcode_column=cols["barcode"], name_column=cols["name"],
            stock_column=cols["stock"], price_column=cols["price"],
            expiry_date_column=cols["expiry"])

    # ── Render results (called from worker finished, before thread done)
    def _on_preview_done(self, result: ProductImportPreview):
        self._progress.hide()
        self._cancel_btn.hide()
        self._no_write_lbl.show()
        if result.cancelled:
            self._status_lbl.setText(
                f"⚠ Η προεπισκόπηση ακυρώθηκε. "
                f"{result.scanned_rows} γραμμές, {result.valid_rows} έγκυρα, "
                f"{result.invalid_rows} άκυρα.")
        elif not result.ok:
            prefix = result.error_message or "Προεπισκόπηση απέτυχε."
            self._status_lbl.setText(
                f"❌ {prefix} ({result.scanned_rows}/{result.valid_rows}/"
                f"{result.invalid_rows})")
        else:
            self._status_lbl.setText(
                f"✅ {result.scanned_rows} σαρώθηκαν, {result.valid_rows} έγκυρα, "
                f"{result.invalid_rows} άκυρα, {result.duplicate_barcodes} διπλότυπα.")
        if result.sample_rows:
            self._sample_lbl.setText(
                f"Δείγμα ({len(result.sample_rows)} από {result.valid_rows}):")
            self._sample_lbl.show()
            self._sample_table.setRowCount(len(result.sample_rows))
            for r, row in enumerate(result.sample_rows):
                for c, val in enumerate(row):
                    self._sample_table.setItem(r, c, QTableWidgetItem(str(val)))
            self._sample_table.show()
        if result.errors:
            self._error_lbl.setText(f"Σφάλματα ({len(result.errors)}):")
            self._error_lbl.show()
            self._error_table.setRowCount(len(result.errors))
            for r, e in enumerate(result.errors):
                self._error_table.setItem(r, 0, QTableWidgetItem(str(e.row_number)))
                self._error_table.setItem(r, 1, QTableWidgetItem(e.barcode))
                self._error_table.setItem(r, 2, QTableWidgetItem(e.code))
                self._error_table.setItem(r, 3, QTableWidgetItem(e.message))
            self._error_table.show()
        # Do NOT re-enable controls here — wait for thread.done

    # ── Worker lifecycle ──────────────────────────────────────────────
    def _start_worker(self, worker, slot):
        # Defensive: refuse if thread ref still present
        if self._thread is not None:
            return
        self._worker = worker
        self._thread = QThread(self)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.finished.connect(slot)
        worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_thread_done(self):
        """QThread fully stopped — safe to clear refs and unlock UI."""
        self._worker = None
        self._thread = None
        self._cancel_event = None
        if self._closing:
            self._closing = False
            self.shutdown_ready.emit()
            super().reject()
            return
        self._set_controls_enabled(True)

    # ── Reset / hide ──────────────────────────────────────────────────
    def _hide_results(self):
        for w in [self._sample_lbl, self._sample_table,
                  self._error_lbl, self._error_table, self._no_write_lbl,
                  self._conflict_summary_lbl, self._conflict_table,
                  self._detail_notice_lbl, self._detail_table,
                  self._detail_truncated_lbl,
                  self._plan_summary_lbl]:
            w.hide()
        self._sample_table.setRowCount(0)
        self._error_table.setRowCount(0)
        self._conflict_table.setRowCount(0)
        self._detail_table.setRowCount(0)
        self._progress.hide()
        self._last_conflict_result = None
        self._current_plan = None
        self._commit_check.hide()
        self._commit_btn.hide()
        self._plan_btn.setEnabled(False)

    def _reset_state(self):
        self._hide_results()
        self._status_lbl.setText("")
        self._cancel_btn.hide()

    # ── Close / shutdown ──────────────────────────────────────────────
    def reject(self):
        if self.is_busy():
            if self._cancel_event:
                self._cancel_event.set()
            self._closing = True
            self._cancel_btn.setEnabled(False)
            self._set_controls_enabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            if self._cancel_event:
                self._cancel_event.set()
            self._closing = True
            self._cancel_btn.setEnabled(False)
            self._set_controls_enabled(False)
            event.ignore()
            return
        super().closeEvent(event)
