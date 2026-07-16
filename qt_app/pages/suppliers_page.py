"""Suppliers page — μητρώο προμηθευτών (read-only Phase A)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QFormLayout, QDialogButtonBox,
    QMessageBox,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_suppliers_page, load_supplier_detail,
    SupplierPageResult, SupplierDetailResult,
)


class _SupplierWorker(QObject):
    finished = Signal(SupplierPageResult)

    def __init__(self, db_path, search_text, page, page_size, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, page, page_size)

    def run(self):
        self.finished.emit(load_suppliers_page(*self._args))


class SuppliersPage(BasePage):
    shutdown_ready = Signal()

    COLS = ["Επωνυμία", "ΑΦΜ", "Υπεύθυνος", "Τηλέφωνο", "Email", "Προϊόντα"]

    @classmethod
    def page_title(cls) -> str:
        return "Μητρώο Προμηθευτών"

    def __init__(self, db_service, config, parent=None):
        self._db_path = (config.get("db_path", "encomm_erp.db")
                         if config else "encomm_erp.db")
        self._worker = None
        self._thread = None
        self._loading = False
        self._close_pending = False
        self._page = 1
        self._page_size = 50
        self._search_text = ""
        super().__init__(db_service, config, parent)

    def build_ui(self):
        # Toolbar
        tb = QHBoxLayout()
        tb.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Αναζήτηση προμηθευτή, ΑΦΜ ή email…")
        self._search.setMinimumHeight(36)
        self._search.returnPressed.connect(self.refresh)
        tb.addWidget(self._search, 2)
        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(self._refresh_btn)
        self.root_layout.addLayout(tb)

        self._detail_btn = QPushButton("📋  Προβολή στοιχείων")
        self._detail_btn.setCursor(Qt.PointingHandCursor)
        self._detail_btn.setStyleSheet(self._btn_qss())
        self._detail_btn.setEnabled(False)
        self._detail_btn.clicked.connect(self._show_detail)
        self.root_layout.addWidget(self._detail_btn)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        self.root_layout.addWidget(self._summary)

        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        self._table = QTableWidget(0, len(self.COLS))
        self._table.setHorizontalHeaderLabels(self.COLS)
        h = self._table.horizontalHeader()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(self.COLS)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.itemSelectionChanged.connect(
            lambda: self._detail_btn.setEnabled(
                len(self._table.selectedItems()) > 0))
        self._table.cellDoubleClicked.connect(lambda r, c: self._show_detail())
        self.root_layout.addWidget(self._table, 1)

        # Pagination
        pg = QHBoxLayout()
        pg.setSpacing(10)
        self._prev_btn = QPushButton("◀  Προηγούμενη")
        self._prev_btn.clicked.connect(lambda: self._go_page(-1))
        pg.addWidget(self._prev_btn)
        pg.addStretch()
        self._page_lbl = QLabel("Σελίδα 1")
        self._page_lbl.setStyleSheet(f"color: {styles.TEXT_PRIMARY}; font-size: 13px;")
        pg.addWidget(self._page_lbl)
        pg.addStretch()
        self._next_btn = QPushButton("Επόμενη  ▶")
        self._next_btn.clicked.connect(lambda: self._go_page(1))
        pg.addWidget(self._next_btn)
        self.root_layout.addLayout(pg)
        self._built = True
        self.refresh()

    @staticmethod
    def _btn_qss():
        return (
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; font-size: 13px; "
            f"font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    def _go_page(self, delta):
        self._page += delta
        self._do_refresh()

    def refresh(self):
        self._page = 1
        self._search_text = self._search.text().strip()
        self._do_refresh()

    def _do_refresh(self):
        if self._loading:
            return
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση προμηθευτών...", styles.TEXT_MUTED)
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _SupplierWorker(
            self._db_path, self._search_text, self._page, self._page_size)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_done)
        self._thread.start()

    def _on_ready(self, result):
        if self._close_pending:
            return
        if not result.ok:
            self._set_state(result.error_message, styles.RED)
            self._table.setRowCount(0)
            self._summary.setText("")
            return
        total = result.total
        first = (result.page - 1) * result.page_size + 1 if total else 0
        last = min(first + result.page_size - 1, total)
        self._summary.setText(f"Εμφάνιση {first}–{last} από {total} προμηθευτές")
        items = result.items
        if not items:
            self._set_state("Δεν βρέθηκαν προμηθευτές.", styles.TEXT_MUTED)
            self._table.setRowCount(0)
        else:
            self._state_lbl.hide()
            self._table.show()
            self._table.setRowCount(len(items))
            for r, s in enumerate(items):
                name_item = QTableWidgetItem(s.name)
                name_item.setData(Qt.UserRole, s.id)
                self._table.setItem(r, 0, name_item)
                self._table.setItem(r, 1, QTableWidgetItem(s.tax_id))
                self._table.setItem(r, 2, QTableWidgetItem(s.contact_person))
                self._table.setItem(r, 3, QTableWidgetItem(s.phone))
                self._table.setItem(r, 4, QTableWidgetItem(s.email))
                self._table.setItem(r, 5, QTableWidgetItem(str(s.product_count)))
        self._page = result.page
        tp = max(1, (total + result.page_size - 1) // result.page_size)
        self._page_lbl.setText(f"Σελίδα {result.page} από {tp}")
        self._prev_btn.setEnabled(result.page > 1)
        self._next_btn.setEnabled(result.page < tp)

    def _on_done(self):
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()

    def _show_detail(self):
        rows = {it.row() for it in self._table.selectedItems()}
        if len(rows) != 1:
            return
        r = list(rows)[0]
        sid_text = self._table.item(r, 0).text()
        # We need the supplier id — stored as item data or a parallel list
        # For simplicity, re-query by name (names are unique in this schema)
        # Actually, store ids in the table as UserRole data
        sid = self._table.item(r, 0).data(Qt.UserRole)
        if sid is None:
            QMessageBox.warning(self, "Σφάλμα", "Αδυναμία εύρεσης ID προμηθευτή.")
            return
        detail = load_supplier_detail(self._db_path, sid)
        if not detail.ok:
            QMessageBox.warning(self, "Σφάλμα", detail.error_message)
            return
        self._show_detail_dialog(detail.supplier)

    def _show_detail_dialog(self, s):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Προμηθευτής: {s.name}")
        dlg.setMinimumWidth(450)
        lay = QFormLayout(dlg)
        fields = [
            ("Επωνυμία", s.name), ("ΑΦΜ", s.tax_id),
            ("Υπεύθυνος", s.contact_person), ("Τηλέφωνο", s.phone),
            ("Email", s.email), ("Διεύθυνση", s.address),
            ("Επιτρεπόμενα email", s.allowed_sender_emails),
            ("Μορφή καταλόγου", s.catalogue_format),
            ("Default Markup", s.default_markup),
            ("Σημειώσεις τιμολόγησης", s.pricing_notes),
            ("Δημιουργήθηκε", s.created_at),
            ("Συνδεδεμένα προϊόντα", str(s.product_count)),
        ]
        for label, val in fields:
            if val and val != "—":
                lbl = QLabel(f"<b>{label}:</b> {val}")
                lbl.setWordWrap(True)
                lay.addRow(lbl)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        lay.addRow(btns)
        dlg.exec()

    # ── Shutdown ─────────────────────────────────────────────────────
    def _cleanup_worker(self):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def shutdown(self) -> bool:
        if self._thread is None or not self._thread.isRunning():
            return True
        try:
            self._worker.finished.disconnect(self._on_ready)
        except (RuntimeError, TypeError):
            pass
        self._close_pending = True
        self._thread.quit()
        if self._thread.wait(2000):
            self._worker = None
            self._thread = None
            self._loading = False
            self._close_pending = False
            return True
        return False

    def _set_state(self, text, color):
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.hide()