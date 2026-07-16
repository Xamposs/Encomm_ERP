"""Stock Movements page — ιστορικό κινήσεων αποθήκης (read-only)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_stock_movements, StockMovementsResult,
)


class _StockWorker(QObject):
    finished = Signal(StockMovementsResult)

    def __init__(self, db_path, search_text, reason_filter,
                 date_from, date_to, page, page_size, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, reason_filter,
                      date_from, date_to, page, page_size)

    def run(self):
        self.finished.emit(load_stock_movements(*self._args))


class StockMovementsPage(BasePage):
    shutdown_ready = Signal()

    COLS = ["Ημερομηνία", "Barcode", "Προϊόν", "Πριν",
            "Μεταβολή", "Μετά", "Αιτία", "Πηγή", "Χειριστής"]

    @classmethod
    def page_title(cls) -> str:
        return "Ιστορικό Κινήσεων Αποθήκης"

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
        # Toolbar — row 1: search + reason
        tb1 = QHBoxLayout()
        tb1.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Αναζήτηση barcode ή προϊόντος…")
        self._search.setMinimumHeight(36)
        self._search.returnPressed.connect(self.refresh)
        tb1.addWidget(self._search, 3)

        self._reason = QComboBox()
        self._reason.setMinimumHeight(36)
        self._reason.addItem("Όλες οι αιτίες", "")
        self._reason.currentIndexChanged.connect(self._on_filter_changed)
        tb1.addWidget(self._reason, 2)
        self.root_layout.addLayout(tb1)

        # Toolbar — row 2: date pickers + refresh
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
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in range(1, len(self.COLS)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
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

    def _on_filter_changed(self):
        self._page = 1
        self._do_refresh()

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
        self._search_text = self._search.text().strip()
        self._do_refresh()

    def _do_refresh(self):
        if self._loading:
            return
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση κινήσεων...", styles.TEXT_MUTED)
        rv = self._reason.currentData()
        df = self._date_from.text().strip()
        dt = self._date_to.text().strip()
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _StockWorker(
            self._db_path, self._search_text, rv, df, dt,
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
        # Populate reasons on first successful load
        if result.ok and result.reasons and self._reason.count() <= 1:
            current = self._reason.currentData()
            for rv in result.reasons:
                self._reason.addItem(rv, rv)
            idx = self._reason.findData(current)
            if idx >= 0:
                self._reason.setCurrentIndex(idx)

        if not result.ok:
            self._set_state(result.error_message, styles.RED)
            self._table.setRowCount(0)
            self._summary.setText("")
            return
        total = result.total
        first = (result.page - 1) * result.page_size + 1 if total else 0
        last = min(first + result.page_size - 1, total)
        self._summary.setText(f"Εμφάνιση {first}–{last} από {total} κινήσεις")
        items = result.items
        if not items:
            self._set_state(
                "Δεν βρέθηκαν κινήσεις αποθήκης.", styles.TEXT_MUTED)
            self._table.setRowCount(0)
        else:
            self._state_lbl.hide()
            self._table.show()
            self._table.setRowCount(len(items))
            for r, m in enumerate(items):
                self._table.setItem(r, 0, QTableWidgetItem(m.timestamp))
                self._table.setItem(r, 1, QTableWidgetItem(m.barcode))
                self._table.setItem(r, 2, QTableWidgetItem(m.product_name))
                self._table.setItem(r, 3, QTableWidgetItem(str(m.old_stock)))
                chg = f"+{m.change_amount}" if m.change_amount > 0 else str(m.change_amount)
                self._table.setItem(r, 4, QTableWidgetItem(chg))
                self._table.setItem(r, 5, QTableWidgetItem(str(m.new_stock)))
                self._table.setItem(r, 6, QTableWidgetItem(m.reason))
                self._table.setItem(r, 7, QTableWidgetItem(m.source))
                self._table.setItem(r, 8, QTableWidgetItem(m.operator))
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
