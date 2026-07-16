"""Invoice History page — ιστορικό παραστατικών (read-only Phase A)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QMessageBox,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_invoices_page, load_invoice_detail,
    InvoicePageResult, InvoiceDetailResult,
)


class _InvoiceWorker(QObject):
    finished = Signal(InvoicePageResult)

    def __init__(self, db_path, search_text, date_from, date_to,
                 page, page_size, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, date_from, date_to, page, page_size)

    def run(self):
        self.finished.emit(load_invoices_page(*self._args))


class InvoiceHistoryPage(BasePage):
    shutdown_ready = Signal()

    COLS = ["Αρ. Παραστατικού", "Ημερομηνία", "Πελάτης",
            "Υποσύνολο", "ΦΠΑ", "Σύνολο"]

    @classmethod
    def page_title(cls) -> str:
        return "Ιστορικό Παραστατικών"

    def __init__(self, db_service, config, parent=None):
        self._db_path = (config.get("db_path", "encomm_erp.db")
                         if config else "encomm_erp.db")
        self._worker = None
        self._thread = None
        self._loading = False
        self._close_pending = False
        self._page = 1
        self._page_size = 50
        super().__init__(db_service, config, parent)

    def build_ui(self):
        # Row 1: search
        tb1 = QHBoxLayout()
        tb1.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Αναζήτηση αριθμού παραστατικού ή πελάτη…")
        self._search.setMinimumHeight(36)
        self._search.returnPressed.connect(self.refresh)
        tb1.addWidget(self._search, 3)
        tb1.addStretch()
        self.root_layout.addLayout(tb1)

        # Row 2: dates + refresh
        tb2 = QHBoxLayout()
        tb2.setSpacing(8)
        tb2.addWidget(QLabel("Από:"))
        self._date_from = QLineEdit()
        self._date_from.setPlaceholderText("YYYY-MM-DD")
        self._date_from.setMaximumWidth(120)
        self._date_from.returnPressed.connect(self.refresh)
        tb2.addWidget(self._date_from)
        tb2.addWidget(QLabel("Έως:"))
        self._date_to = QLineEdit()
        self._date_to.setPlaceholderText("YYYY-MM-DD")
        self._date_to.setMaximumWidth(120)
        self._date_to.returnPressed.connect(self.refresh)
        tb2.addWidget(self._date_to)
        self._clear_dates = QPushButton("✕")
        self._clear_dates.setMaximumWidth(30)
        self._clear_dates.clicked.connect(self._on_clear_dates)
        tb2.addWidget(self._clear_dates)
        tb2.addStretch()
        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self.refresh)
        tb2.addWidget(self._refresh_btn)
        self.root_layout.addLayout(tb2)

        # Detail button
        self._detail_btn = QPushButton("📋  Προβολή παραστατικού")
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

    def _on_clear_dates(self):
        self._date_from.clear()
        self._date_to.clear()
        self._page = 1
        self._do_refresh()

    def _go_page(self, delta):
        self._page += delta
        self._do_refresh()

    def refresh(self):
        self._page = 1
        self._do_refresh()

    def _do_refresh(self):
        if self._loading:
            return
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση παραστατικών...", styles.TEXT_MUTED)
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _InvoiceWorker(
            self._db_path, self._search.text().strip(),
            self._date_from.text().strip(), self._date_to.text().strip(),
            self._page, self._page_size)
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
        self._summary.setText(f"Εμφάνιση {first}–{last} από {total} παραστατικά")
        items = result.items
        if not items:
            self._set_state("Δεν βρέθηκαν παραστατικά.", styles.TEXT_MUTED)
            self._table.setRowCount(0)
        else:
            self._state_lbl.hide()
            self._table.show()
            self._table.setRowCount(len(items))
            for r, i in enumerate(items):
                id_item = QTableWidgetItem(i.id)
                id_item.setData(Qt.UserRole, i.id)
                self._table.setItem(r, 0, id_item)
                self._table.setItem(r, 1, QTableWidgetItem(i.invoice_date))
                self._table.setItem(r, 2, QTableWidgetItem(i.customer_name))
                self._table.setItem(r, 3, QTableWidgetItem(f"€{i.subtotal:.2f}"))
                self._table.setItem(r, 4, QTableWidgetItem(f"€{i.vat_amount:.2f}"))
                self._table.setItem(r, 5, QTableWidgetItem(f"€{i.grand_total:.2f}"))
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
        inv_id = self._table.item(r, 0).data(Qt.UserRole)
        if not inv_id:
            QMessageBox.warning(self, "Σφάλμα", "Αδυναμία εύρεσης παραστατικού.")
            return
        detail = load_invoice_detail(self._db_path, inv_id)
        if not detail.ok:
            QMessageBox.warning(self, "Σφάλμα", detail.error_message)
            return
        self._show_detail_dialog(detail.invoice)

    def _show_detail_dialog(self, inv):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Παραστατικό: {inv.id}")
        dlg.setMinimumWidth(550)
        lay = QVBoxLayout(dlg)

        # Header
        hdr = QFormLayout()
        hdr.addRow(QLabel("<b>Ημερομηνία:</b>"), QLabel(inv.invoice_date))
        hdr.addRow(QLabel("<b>Πελάτης:</b>"), QLabel(inv.customer_name))
        hdr.addRow(QLabel("<b>Υποσύνολο:</b>"), QLabel(f"€{inv.subtotal:.2f}"))
        hdr.addRow(QLabel("<b>ΦΠΑ:</b>"), QLabel(f"€{inv.vat_amount:.2f}"))
        hdr.addRow(QLabel("<b>Σύνολο:</b>"), QLabel(f"€{inv.grand_total:.2f}"))
        lay.addLayout(hdr)

        # Items table
        if inv.items:
            cols = ["Barcode", "Προϊόν", "Ποσότητα", "Τιμή Μονάδας", "Σύνολο Γραμμής"]
            tbl = QTableWidget(len(inv.items), len(cols))
            tbl.setHorizontalHeaderLabels(cols)
            tbl.horizontalHeader().setStretchLastSection(True)
            tbl.verticalHeader().setVisible(False)
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
            for r, it in enumerate(inv.items):
                tbl.setItem(r, 0, QTableWidgetItem(it.barcode))
                tbl.setItem(r, 1, QTableWidgetItem(it.name))
                tbl.setItem(r, 2, QTableWidgetItem(str(it.quantity)))
                tbl.setItem(r, 3, QTableWidgetItem(f"€{it.price:.2f}"))
                tbl.setItem(r, 4, QTableWidgetItem(f"€{it.line_total:.2f}"))
            lay.addWidget(tbl)
        else:
            lay.addWidget(QLabel("Δεν υπάρχουν γραμμές παραστατικού."))

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()

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
