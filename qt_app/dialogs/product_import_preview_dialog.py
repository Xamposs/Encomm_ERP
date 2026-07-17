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


@dataclass
class _InspectResult:
    ok: bool
    token: int = 0
    file_path: str = ""
    sheet_name: str = ""
    error: str = ""
    sheets: Tuple[str, ...] = ()
    headers: Tuple[str, ...] = ()
    suggested_mapping: ImportColumnMapping | None = None


class _InspectWorker(QObject):
    finished = Signal(_InspectResult)

    def __init__(self, token, file_path, sheet_name=None, parent=None):
        super().__init__(parent)
        self._token = token
        self._fp = file_path
        self._sn = sheet_name

    def run(self):
        try:
            sheets = list_xlsx_sheets(self._fp)
            sn = self._sn
            if sn is None and sheets:
                sn = sheets[0]
            headers = inspect_xlsx_headers(self._fp, sn) if sn else ()
            suggested = suggest_mapping(self._fp, sn) if sn else None
            self.finished.emit(_InspectResult(
                ok=True, token=self._token,
                file_path=self._fp, sheet_name=sn or "",
                sheets=sheets, headers=headers,
                suggested_mapping=suggested))
        except Exception as e:
            self.finished.emit(_InspectResult(
                ok=False, token=self._token, error=str(e)))


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
    shutdown_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Προεπισκόπηση Εισαγωγής Excel")
        self.setMinimumSize(750, 600)
        self._file_path: str = ""
        self._sheet_name: str = ""
        self._cancel_event: threading.Event | None = None
        self._worker: QObject | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._closing = False
        self._inspect_token = 0  # monotonically increasing
        self._build_ui()
        self._reset_state()

    # ── Public shutdown contract ──────────────────────────────────────
    def is_busy(self) -> bool:
        return (self._thread is not None and self._thread.isRunning())

    def request_shutdown(self) -> None:
        if not self.is_busy():
            super().reject()
            return
        if self._cancel_event:
            self._cancel_event.set()
        self._closing = True
        self._status_lbl.setText("Ακύρωση προεπισκόπησης…")
        self._status_lbl.setStyleSheet(f"color: {styles.AMBER}; font-size: 12px;")
        self._cancel_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)

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
        self._progress.setRange(0, 0)
        self._progress.hide()
        lay.addWidget(self._progress)

        # Status
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
        btns_row.addStretch()
        lay.addLayout(btns_row)

        # Results tables
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
        self._sheet_combo.setEnabled(enabled)
        for cb in self._map_combos.values():
            cb.setEnabled(enabled)
        if enabled:
            self._preview_btn.setEnabled(
                bool(self._file_path) and len(self._sheet_combo.currentText()) > 0)
        else:
            self._preview_btn.setEnabled(False)

    # ── File / sheet ──────────────────────────────────────────────────
    def _on_choose_file(self):
        if self._loading:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Επιλογή αρχείου Excel", "",
            "Excel Files (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            QMessageBox.warning(
                self, "Σφάλμα",
                "Το αρχείο πρέπει να έχει κατάληξη .xlsx.")
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Σφάλμα", "Το αρχείο δεν βρέθηκε.")
            return
        self._file_path = path
        self._file_lbl.setText(os.path.basename(path))
        self._inspect_file()  # sheet_name=None → auto-pick first

    def _inspect_file(self, sheet_name=None):
        if self._loading:
            return
        self._loading = True
        self._inspect_token += 1
        self._set_controls_enabled(False)
        self._status_lbl.setText("🔄 Ανάγνωση δομής αρχείου…")
        self._start_worker(
            _InspectWorker(self._inspect_token, self._file_path, sheet_name),
            self._on_inspect_done)

    def _on_inspect_done(self, result: _InspectResult):
        if result.token != self._inspect_token:
            return  # stale
        if not result.ok:
            QMessageBox.warning(self, "Σφάλμα",
                                f"Αδυναμία ανάγνωσης: {result.error}")
            self._on_worker_done()
            return
        # Populate sheets only on first load (from file selection, token 1)
        if result.token == 1:
            self._sheet_combo.blockSignals(True)
            self._sheet_combo.clear()
            self._sheet_combo.addItems(result.sheets)
            self._sheet_combo.blockSignals(False)
        # Set the sheet explicitly
        if result.sheet_name:
            self._sheet_name = result.sheet_name
            idx = self._sheet_combo.findText(result.sheet_name)
            if idx >= 0:
                self._sheet_combo.blockSignals(True)
                self._sheet_combo.setCurrentIndex(idx)
                self._sheet_combo.blockSignals(False)
        self._apply_headers(result.headers, result.suggested_mapping)
        self._reset_state()
        self._on_worker_done()

    def _on_sheet_changed(self, name):
        if not name or not self._file_path or self._loading:
            return
        self._sheet_name = name
        self._inspect_file(sheet_name=name)

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
        if self._loading:
            return
        mapping = self._get_mapping()
        if mapping is None:
            QMessageBox.warning(self, "Σφάλμα",
                "Επιλέξτε μοναδικές στήλες για κάθε πεδίο. "
                "Δεν επιτρέπονται διπλότυπες αντιστοιχίσεις.")
            return
        self._loading = True
        self._set_controls_enabled(False)
        self._preview_btn.hide()
        self._cancel_btn.show()
        self._progress.show()
        self._status_lbl.setText("🔄 Εκτέλεση προεπισκόπησης…")
        self._cancel_event = threading.Event()
        self._start_worker(
            _PreviewWorker(self._file_path, mapping, self._sheet_name,
                           self._cancel_event),
            self._on_preview_done)

    def _on_cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText("Ακύρωση προεπισκόπησης…")

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

    # ── Render results ────────────────────────────────────────────────
    def _on_preview_done(self, result: ProductImportPreview):
        self._render_result(result)
        self._on_worker_done()

    def _render_result(self, result: ProductImportPreview):
        self._progress.hide()
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

    # ── Worker lifecycle ──────────────────────────────────────────────
    def _start_worker(self, worker, slot):
        # Never overwrite refs while previous thread runs
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

    def _on_worker_done(self):
        """Called from result handler to re-enable UI."""
        self._loading = False
        if not self._closing:
            self._set_controls_enabled(True)

    def _on_thread_done(self):
        self._worker = None
        self._thread = None
        self._cancel_event = None
        self._loading = False
        if self._closing:
            self._closing = False
            self._set_controls_enabled(True)
            self.shutdown_ready.emit()
            super().reject()

    # ── Reset / hide ──────────────────────────────────────────────────
    def _hide_results(self):
        for w in [self._sample_lbl, self._sample_table,
                  self._error_lbl, self._error_table, self._no_write_lbl]:
            w.hide()

    def _reset_state(self):
        self._hide_results()
        self._progress.hide()
        self._status_lbl.setText("")
        self._cancel_btn.hide()

    # ── Close / shutdown ──────────────────────────────────────────────
    def reject(self):
        if self.is_busy():
            if self._cancel_event:
                self._cancel_event.set()
            self._closing = True
            self._status_lbl.setText("Ακύρωση προεπισκόπησης…")
            self._cancel_btn.setEnabled(False)
            self._preview_btn.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            if self._cancel_event:
                self._cancel_event.set()
            self._closing = True
            self._status_lbl.setText("Ακύρωση προεπισκόπησης…")
            self._cancel_btn.setEnabled(False)
            self._preview_btn.setEnabled(False)
            event.ignore()
            return
        super().closeEvent(event)
