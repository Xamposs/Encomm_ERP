"""Preview dialog for XLSX product import — read-only, no database writes."""

from __future__ import annotations

import os, threading
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QGroupBox, QProgressBar,
    QDialogButtonBox,
)

from qt_app import styles
from infrastructure.product_import_preview import (
    preview_product_import_xlsx, ImportColumnMapping,
    suggest_mapping, list_xlsx_sheets, inspect_xlsx_headers,
    ProductImportPreview,
)


FIELD_KEYS = ("barcode", "name", "stock", "price", "expiry")
FIELD_LABELS = {
    "barcode": "Barcode",
    "name": "Όνομα Προϊόντος",
    "stock": "Απόθεμα",
    "price": "Τιμή",
    "expiry": "Ημ. Λήξης",
}


class _PreviewWorker(QObject):
    finished = Signal(ProductImportPreview)

    def __init__(self, file_path, mapping, sheet_name, cancel_event, parent=None):
        super().__init__(parent)
        self._fp = file_path
        self._m = mapping
        self._sn = sheet_name
        self._cancel = cancel_event

    def run(self):
        self.finished.emit(preview_product_import_xlsx(
            self._fp, self._m, self._sn, cancel_event=self._cancel))


class ProductImportPreviewDialog(QDialog):
    """Modal dialog: choose file → map columns → preview → review results."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Προεπισκόπηση Εισαγωγής Excel")
        self.setMinimumSize(750, 600)
        self._file_path: str = ""
        self._sheet_name: str = ""
        self._cancel_event: threading.Event | None = None
        self._worker: _PreviewWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._build_ui()
        self._reset_state()

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
        btn = QPushButton("📂  Επιλογή αρχείου .xlsx")
        btn.setStyleSheet(self._btn_qss())
        btn.clicked.connect(self._on_choose_file)
        fl.addWidget(btn)
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
            self._map_combos[key] = cb
            row.addWidget(cb, 1)
            row.addStretch()
            ml.addLayout(row)
        grp3.setLayout(ml)
        lay.addWidget(grp3)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.hide()
        lay.addWidget(self._progress)

        # Status
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        lay.addWidget(self._status_lbl)

        # Preview button
        btns_row = QHBoxLayout()
        self._preview_btn = QPushButton("🔍  Εκτέλεση Προεπισκόπησης")
        self._preview_btn.setStyleSheet(self._btn_qss())
        self._preview_btn.clicked.connect(self._on_run_preview)
        self._preview_btn.setEnabled(False)
        btns_row.addWidget(self._preview_btn)
        btns_row.addStretch()
        lay.addLayout(btns_row)

        # Results tables
        self._results_stack = QVBoxLayout()
        self._sample_lbl = QLabel("")
        self._sample_lbl.setStyleSheet(f"color: {styles.ACCENT}; font-weight: bold;")
        self._results_stack.addWidget(self._sample_lbl)
        self._sample_table = QTableWidget(0, 5)
        self._sample_table.setHorizontalHeaderLabels(
            ["Barcode", "Όνομα", "Στοκ", "Τιμή", "Ημ. Λήξης"])
        self._sample_table.horizontalHeader().setStretchLastSection(True)
        self._sample_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._sample_table.hide()
        self._results_stack.addWidget(self._sample_table)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {styles.RED}; font-weight: bold;")
        self._results_stack.addWidget(self._error_lbl)
        self._error_table = QTableWidget(0, 4)
        self._error_table.setHorizontalHeaderLabels(
            ["Γραμμή", "Barcode", "Κωδικός", "Μήνυμα"])
        self._error_table.horizontalHeader().setStretchLastSection(True)
        self._error_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._error_table.hide()
        self._results_stack.addWidget(self._error_table)

        self._no_write_lbl = QLabel(
            "Δεν έγιναν αλλαγές στην αποθήκη. Η προεπισκόπηση είναι μόνο για έλεγχο.")
        self._no_write_lbl.setWordWrap(True)
        self._no_write_lbl.setStyleSheet(f"color: {styles.AMBER}; font-size: 12px;")
        self._no_write_lbl.hide()
        self._results_stack.addWidget(self._no_write_lbl)
        lay.addLayout(self._results_stack)

        # Close
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

    # ── File / sheet ──────────────────────────────────────────────────
    def _on_choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Επιλογή αρχείου Excel", "", "Excel Files (*.xlsx)")
        if not path:
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Σφάλμα", "Το αρχείο δεν βρέθηκε.")
            return
        self._file_path = path
        self._file_lbl.setText(os.path.basename(path))
        try:
            sheets = list_xlsx_sheets(path)
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα",
                                f"Αδυναμία ανάγνωσης αρχείου: {e}")
            return
        self._sheet_combo.clear()
        self._sheet_combo.addItems(sheets)
        if sheets:
            self._sheet_combo.setCurrentIndex(0)
        self._reset_state()

    def _on_sheet_changed(self, name):
        if not name or not self._file_path:
            return
        self._sheet_name = name
        try:
            headers = inspect_xlsx_headers(self._file_path, name)
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα",
                                f"Αδυναμία ανάγνωσης κεφαλίδων: {e}")
            return
        suggested = suggest_mapping(self._file_path, name)
        for key in FIELD_KEYS:
            cb = self._map_combos[key]
            cb.clear()
            cb.addItem("— Αυτόματο —", "")
            cb.addItems(headers)
            if suggested:
                col = getattr(suggested, f"{key}_column", None)
                if col and col in headers:
                    cb.setCurrentText(col)
        self._reset_state()
        self._preview_btn.setEnabled(True)

    # ── Run preview ───────────────────────────────────────────────────
    def _on_run_preview(self):
        if self._loading:
            return
        mapping = self._get_mapping()
        if mapping is None:
            QMessageBox.warning(self, "Σφάλμα",
                                "Επιλέξτε μοναδικές στήλες για κάθε πεδίο. "
                                "Δεν επιτρέπονται διπλότυπες αντιστοιχίσεις.")
            return

        self._loading = True
        self._preview_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("🔄 Εκτέλεση προεπισκόπησης…")
        self._status_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        self._hide_results()
        self._cancel_event = threading.Event()

        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _PreviewWorker(
            self._file_path, mapping, self._sheet_name, self._cancel_event)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_preview_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

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
            barcode_column=cols["barcode"],
            name_column=cols["name"],
            stock_column=cols["stock"],
            price_column=cols["price"],
            expiry_date_column=cols["expiry"],
        )

    def _on_preview_done(self, result: ProductImportPreview):
        self._progress.hide()
        self._loading = False
        self._preview_btn.setEnabled(True)
        if result.cancelled:
            self._status_lbl.setText(
                "⚠ Η προεπισκόπηση ακυρώθηκε. "
                f"Ελέγχθηκαν {result.scanned_rows} γραμμές.")
            self._status_lbl.setStyleSheet(f"color: {styles.AMBER}; font-size: 12px;")
            return
        if not result.ok and not result.sample_rows and not result.errors:
            self._status_lbl.setText(f"❌ {result.error_message}")
            self._status_lbl.setStyleSheet(f"color: {styles.RED}; font-size: 12px;")
            return

        self._status_lbl.setText(
            f"✅ Προεπισκόπηση ολοκληρώθηκε: "
            f"{result.scanned_rows} σαρώθηκαν, {result.valid_rows} έγκυρα, "
            f"{result.invalid_rows} άκυρα, {result.duplicate_barcodes} διπλότυπα.")
        self._status_lbl.setStyleSheet(f"color: {styles.ACCENT}; font-size: 12px;")

        # Samples
        if result.sample_rows:
            self._sample_lbl.setText(
                f"Δείγμα ({min(len(result.sample_rows), 20)} από {result.valid_rows}):")
            self._sample_lbl.show()
            self._sample_table.setRowCount(len(result.sample_rows))
            for r, row in enumerate(result.sample_rows):
                self._sample_table.setItem(r, 0, QTableWidgetItem(str(row[0])))
                self._sample_table.setItem(r, 1, QTableWidgetItem(str(row[1])))
                self._sample_table.setItem(r, 2, QTableWidgetItem(str(row[2])))
                self._sample_table.setItem(r, 3, QTableWidgetItem(str(row[3])))
                self._sample_table.setItem(r, 4, QTableWidgetItem(str(row[4])))
            self._sample_table.show()

        # Errors
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

        self._no_write_lbl.show()

    def _on_thread_done(self):
        self._loading = False
        self._worker = None
        self._thread = None
        self._cancel_event = None

    def _hide_results(self):
        for w in [self._sample_lbl, self._sample_table,
                  self._error_lbl, self._error_table, self._no_write_lbl]:
            w.hide()

    def _reset_state(self):
        self._hide_results()
        self._progress.hide()
        self._status_lbl.setText("")
        self._preview_btn.setEnabled(
            bool(self._file_path) and len(self._sheet_combo.currentText()) > 0)

    # ── Close / shutdown ──────────────────────────────────────────────
    def reject(self):
        if self._loading and self._cancel_event:
            self._cancel_event.set()
        self._cleanup_worker()
        if self._thread and self._thread.isRunning():
            self._thread.wait(2000)
        super().reject()

    def closeEvent(self, event):
        if self._loading and self._cancel_event:
            self._cancel_event.set()
        self._cleanup_worker()
        if self._thread and self._thread.isRunning():
            self._thread.wait(2000)
        super().closeEvent(event)

    def _cleanup_worker(self):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
        self._worker = None
        self._thread = None
