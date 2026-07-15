"""Inventory page — κατάλογος προϊόντων αποθήκης (read-only).

Uses the same QThread + typed-result pattern as DashboardPage.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_inventory_page, InventoryResult,
)


# ── Filters ────────────────────────────────────────────────────────────

FILTERS = [
    ("all",         "Όλα"),
    ("low_stock",   "Χαμηλό Στοκ"),
    ("expired",     "Ληγμένα"),
    ("near_expiry", "Λήγουν Σύντομα"),
    ("available",   "Διαθέσιμα"),
]
FILTER_KEYS = [f[0] for f in FILTERS]


# ═══════════════════════════════════════════════════════════════════════
# QThread worker
# ═══════════════════════════════════════════════════════════════════════

class _InventoryWorker(QObject):
    finished = Signal(InventoryResult)

    def __init__(self, db_path: str, search_text: str, status_filter: str,
                 threshold: int, alert_days: int,
                 page: int, page_size: int, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, status_filter, threshold,
                       alert_days, page, page_size)

    def run(self) -> None:
        result = load_inventory_page(*self._args)
        self.finished.emit(result)


# ═══════════════════════════════════════════════════════════════════════
# Inventory page
# ═══════════════════════════════════════════════════════════════════════

class InventoryPage(BasePage):
    shutdown_ready = Signal()

    COLUMNS = [
        "Barcode", "Όνομα Προϊόντος", "Στοκ", "Ημ. Λήξης",
        "Τιμή", "Κατάσταση", "Προμηθευτής",
    ]

    @classmethod
    def page_title(cls) -> str:
        return "Διαχείριση Αποθήκης"

    def __init__(self, db_service, config: dict, parent=None):
        db_path = (config.get("db_path", "encomm_erp.db")
                   if config else "encomm_erp.db")
        self._db_path = db_path
        self._worker: _InventoryWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False
        self._page = 1
        self._page_size = 50
        self._search_text = ""
        self._status_filter = "all"
        # Queued state: if a refresh is running and the user changes
        # something, we save the latest request here and run it once
        # the current worker finishes.
        self._pending_req: dict | None = None
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._do_refresh)
        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────
    def build_ui(self) -> None:
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText(
            "Αναζήτηση barcode ή ονόματος…")
        self._search_entry.setMinimumHeight(36)
        self._search_entry.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_entry, 2)

        self._filter_combo = QComboBox()
        for _key, label in FILTERS:
            self._filter_combo.addItem(label)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo, 1)

        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; "
            f"font-size: 13px; font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)
        self.root_layout.addLayout(toolbar)

        # Results summary
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        self.root_layout.addWidget(self._summary_lbl)

        # State label
        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        # Table
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.root_layout.addWidget(self._table, 1)

        # Pagination bar
        pag_bar = QHBoxLayout()
        pag_bar.setSpacing(10)
        self._prev_btn = QPushButton("◀  Προηγούμενη")
        self._prev_btn.clicked.connect(self._prev_page)
        pag_bar.addWidget(self._prev_btn)
        pag_bar.addStretch()
        self._page_lbl = QLabel("Σελίδα 1")
        self._page_lbl.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 13px;")
        pag_bar.addWidget(self._page_lbl)
        pag_bar.addStretch()
        self._next_btn = QPushButton("Επόμενη  ▶")
        self._next_btn.clicked.connect(self._next_page)
        pag_bar.addWidget(self._next_btn)
        self.root_layout.addLayout(pag_bar)

        self._built = True
        self.refresh()

    # ── User actions ─────────────────────────────────────────────────
    def _on_search_changed(self, text: str) -> None:
        self._search_text = text
        self._page = 1
        self._debounce_timer.start(300)

    def _on_filter_changed(self, idx: int) -> None:
        self._status_filter = FILTER_KEYS[idx] if 0 <= idx < len(FILTER_KEYS) else "all"
        self._page = 1
        self._debounce_timer.stop()
        self._do_refresh()

    def _prev_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self._do_refresh()

    def _next_page(self) -> None:
        self._page += 1
        self._do_refresh()

    # ── Refresh ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Debounced entry point (called by button too)."""
        self._debounce_timer.stop()
        self._do_refresh()

    def _do_refresh(self) -> None:
        if self._loading:
            # Queue the latest request
            self._pending_req = {
                "search": self._search_text,
                "filter": self._status_filter,
                "page": self._page,
            }
            return
        self._pending_req = None
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση δεδομένων αποθήκης...", styles.TEXT_MUTED)

        threshold = int(self.config.get("low_stock_threshold", 10)) if self.config else 10
        alert_days = int(self.config.get("expiry_alert_days", 30)) if self.config else 30

        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _InventoryWorker(
            self._db_path, self._search_text, self._status_filter,
            threshold, alert_days, self._page, self._page_size)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_data_ready(self, result: InventoryResult) -> None:
        if self._close_pending:
            return
        if not result.ok:
            self._set_state(result.error_message, styles.RED)
            self._clear_table()
            self._summary_lbl.setText("")
            return

        snap = result.snapshot
        total = snap.total_matching
        first = (snap.page - 1) * snap.page_size + 1 if total else 0
        last = min(first + snap.page_size - 1, total)
        self._summary_lbl.setText(
            f"Εμφάνιση {first}–{last} από {total} προϊόντα")

        prods = snap.products
        if not prods:
            self._set_state(
                "Δεν βρέθηκαν προϊόντα με τα επιλεγμένα φίλτρα.",
                styles.TEXT_MUTED)
            self._clear_table()
        else:
            self._state_lbl.hide()
            self._table.show()
            self._table.setRowCount(len(prods))
            for r, p in enumerate(prods):
                self._table.setItem(r, 0, QTableWidgetItem(p.barcode))
                self._table.setItem(r, 1, QTableWidgetItem(p.name))
                self._table.setItem(r, 2, QTableWidgetItem(str(p.stock)))
                self._table.setItem(r, 3, QTableWidgetItem(p.expiry_date))
                self._table.setItem(r, 4, QTableWidgetItem(f"€{p.price:.2f}"))
                status_text = " · ".join(p.status_labels)
                self._table.setItem(r, 5, QTableWidgetItem(status_text))
                self._table.setItem(r, 6, QTableWidgetItem(p.supplier_name))
                # Colour: expired=red, low/near=amber
                if any("Ληγμένο" in s for s in p.status_labels):
                    fg = QColor(styles.RED)
                elif any("Λήγει" in s or "Χαμηλό" in s for s in p.status_labels):
                    fg = QColor(styles.AMBER)
                else:
                    fg = QColor(styles.TEXT_PRIMARY)
                for c in range(len(self.COLUMNS)):
                    self._table.item(r, c).setForeground(fg)

        # Pagination
        self._page = snap.page
        total_pages = max(1, (total + snap.page_size - 1) // snap.page_size)
        self._page_lbl.setText(f"Σελίδα {snap.page} από {total_pages}")
        self._prev_btn.setEnabled(snap.page > 1)
        self._next_btn.setEnabled(snap.page < total_pages)

    def _on_thread_done(self) -> None:
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()
            return
        # Run queued request if any
        if self._pending_req:
            req = self._pending_req
            self._pending_req = None
            self._search_text = req["search"]
            self._status_filter = req["filter"]
            self._page = req["page"]
            self._do_refresh()

    # ── Shutdown (mirrors DashboardPage contract) ────────────────────
    def _cleanup_worker(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def shutdown(self) -> bool:
        if self._thread is None or not self._thread.isRunning():
            return True
        try:
            self._worker.finished.disconnect(self._on_data_ready)
        except (RuntimeError, TypeError):
            pass
        self._close_pending = True
        self._thread.quit()
        if self._thread.wait(2000):
            self._loading = False
            self._worker = None
            self._thread = None
            self._close_pending = False
            return True
        return False

    # ── Helpers ─────────────────────────────────────────────────────
    def _set_state(self, text: str, color: str) -> None:
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(
            f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.hide()

    def _clear_table(self) -> None:
        self._table.setRowCount(0)
