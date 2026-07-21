"""Stock Lot Integrity page (Phase P5.2b) — operator-facing read-only view.

Uses the accepted P5.2a model ``load_stock_lot_integrity`` for all
database access.  Never queries SQLite on the GUI thread; always uses a
QObject worker moved to a QThread.

Lifecycle follows the proven DashboardPage pattern exactly:

IDLE:   _loading=False, _thread=None, _worker=None, refresh() allowed
RUNNING: _loading=True,  _thread/worker reference the single active pair,
         refresh() is a no-op, controls disabled
     → worker finishes → _on_data_ready processes result
     → thread.finished → _on_thread_done clears refs, sets _loading=False,
                          restores controls, handles close_pending

Shutdown: disconnect UI callback, quit + bounded wait(2000).
          On timeout: preserve live refs, return False.
          The normal completion path emits shutdown_ready when close_pending.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QComboBox, QSpinBox, QAbstractItemView,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from infrastructure.stock_lot_integrity_model import (
    load_stock_lot_integrity,
    StockLotIntegrityResult,
    StockLotIntegritySnapshot,
    ProductLotIntegrity,
)


TABLE_COLS = [
    "Barcode",
    "Προϊόν",
    "Κύριο Απόθεμα",
    "Σύνολο Παρτίδων",
    "Με Ημερομηνία",
    "Χωρίς Ημερομηνία",
    "Μη Έγκυρη Ημερομηνία",
    "Απαρακολούθητο",
    "Υπερκάλυψη",
    "Πρώτη Έγκυρη Λήξη",
    "Ληγμένες Μονάδες",
    "Μονάδες που Λήγουν Σύντομα",
    "Κατάσταση",
]

TOOLTIP_UNTRACKED = (
    "Ποσότητα αποθέματος που δεν καλύπτεται από καμία παρτίδα. "
    "Το απόθεμα αυτό δεν μπορεί να παρακολουθηθεί ως προς την ημερομηνία λήξης του."
)
TOOLTIP_OVERAGE = (
    "Η συνολική ποσότητα παρτίδων υπερβαίνει το απόθεμα του προϊόντος. "
    "Αυτό υποδεικνύει πιθανό σφάλμα δεδομένων."
)

FILTER_OPTIONS = [
    ("all",                  "Όλα"),
    ("needs_attention",      "Χρειάζεται Έλεγχο"),
    ("expired",              "Ληγμένες Μονάδες"),
    ("expiring_soon",        "Λήγουν Σύντομα"),
    ("untracked",            "Απαρακολούθητο"),
    ("undated",              "Χωρίς Ημερομηνία"),
    ("invalid_date",         "Μη Έγκυρη Ημερομηνία"),
    ("overage",              "Υπερκάλυψη"),
    ("fully_covered",        "Πλήρως Καταγεγραμμένα"),
]

FILTER_KEYS = [f[0] for f in FILTER_OPTIONS]


def _filter_products(
    products: tuple[ProductLotIntegrity, ...],
    filter_key: str,
) -> list[ProductLotIntegrity]:
    if filter_key == "all":
        return list(products)

    def _check(p: ProductLotIntegrity) -> bool:
        if filter_key == "needs_attention":
            return (
                p.expired_lot_qty > 0
                or p.expiring_soon_lot_qty > 0
                or p.untracked_qty > 0
                or p.qty_in_undated_lots > 0
                or p.qty_in_invalid_date_lots > 0
                or p.lot_overage_qty > 0
            )
        if filter_key == "expired":
            return p.expired_lot_qty > 0
        if filter_key == "expiring_soon":
            return p.expiring_soon_lot_qty > 0
        if filter_key == "untracked":
            return p.untracked_qty > 0
        if filter_key == "undated":
            return p.qty_in_undated_lots > 0
        if filter_key == "invalid_date":
            return p.qty_in_invalid_date_lots > 0
        if filter_key == "overage":
            return p.lot_overage_qty > 0
        if filter_key == "fully_covered":
            return (
                p.master_stock > 0
                and p.untracked_qty == 0
                and p.lot_overage_qty == 0
                and p.qty_in_undated_lots == 0
                and p.qty_in_invalid_date_lots == 0
                and p.qty_in_dated_lots == p.master_stock
            )
        return True

    return [p for p in products if _check(p)]


PAGE_SIZE = 50


def _paginate(
    items: list[ProductLotIntegrity],
    page: int,
) -> tuple[list[ProductLotIntegrity], int, int]:
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE if total > 0 else 1)
    if total == 0:
        return [], 1, 1
    clamped = max(1, min(page, total_pages))
    start = (clamped - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end], total_pages, clamped


class _StockLotIntegrityWorker(QObject):
    finished = Signal(StockLotIntegrityResult)

    def __init__(
        self,
        db_path: str,
        business_date: str,
        alert_days: int,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._db_path = db_path
        self._business_date = business_date
        self._alert_days = alert_days

    def run(self) -> None:
        result = load_stock_lot_integrity(
            self._db_path,
            business_date=self._business_date,
            alert_days=self._alert_days,
        )
        self.finished.emit(result)


def _stat_card(title: str, accent: str = styles.GREEN) -> tuple[QFrame, QLabel]:
    card = QFrame()
    card.setFrameShape(QFrame.StyledPanel)
    card.setStyleSheet(
        f"QFrame {{ background: {styles.DARK_SURFACE}; "
        f"border-radius: 8px; border: 1px solid {styles.BORDER}; "
        f"padding: 10px; }}"
    )
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    title_lbl = QLabel(title)
    title_lbl.setFont(QFont("Segoe UI", 10))
    title_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED};")
    lay.addWidget(title_lbl)
    value_lbl = QLabel("—")
    value_lbl.setFont(QFont("Segoe UI", 22, QFont.Bold))
    value_lbl.setStyleSheet(f"color: {accent};")
    lay.addWidget(value_lbl)
    return card, value_lbl


class StockLotIntegrityPage(BasePage):
    """Operator-facing read-only stock-lot integrity page.

    Lifecycle mirrors DashboardPage: one active pair at a time,
    _loading prevents overlaps, _on_thread_done clears refs synchronously.
    """

    shutdown_ready = Signal()

    def __init__(self, db_service, config: dict, parent=None):
        db_path = (
            config.get("db_path", "encomm_erp.db")
            if config else "encomm_erp.db"
        )
        self._db_path = db_path
        self._worker: _StockLotIntegrityWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False

        self._current_snapshot: StockLotIntegritySnapshot | None = None
        self._all_products: tuple[ProductLotIntegrity, ...] = ()
        self._filter_key = "all"
        self._page = 1
        self._alert_days = 30

        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────

    def build_ui(self) -> None:
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(QLabel("Προειδοποίηση (ημέρες):"))
        self._alert_spin = QSpinBox()
        self._alert_spin.setRange(0, 3650)
        self._alert_spin.setValue(self._alert_days)
        self._alert_spin.setMinimumHeight(36)
        self._alert_spin.valueChanged.connect(self._on_alert_days_changed)
        ctrl_row.addWidget(self._alert_spin)
        ctrl_row.addStretch()
        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        ctrl_row.addWidget(self._refresh_btn)
        self.root_layout.addLayout(ctrl_row)

        sum_row1 = QHBoxLayout()
        sum_row1.setSpacing(8)
        self._card_total,  self._lbl_total  = _stat_card("Σύνολο Προϊόντων")
        self._card_fully,  self._lbl_fully  = _stat_card(
            "Πλήρως Καταγεγραμμένα", styles.GREEN)
        self._card_untr,   self._lbl_untr   = _stat_card(
            "Απαρακολούθητα", styles.AMBER)
        self._card_undated, self._lbl_undated = _stat_card(
            "Αχρονολόγητες Παρτίδες", styles.AMBER)
        sum_row1.addWidget(self._card_total)
        sum_row1.addWidget(self._card_fully)
        sum_row1.addWidget(self._card_untr)
        sum_row1.addWidget(self._card_undated)
        self.root_layout.addLayout(sum_row1)

        sum_row2 = QHBoxLayout()
        sum_row2.setSpacing(8)
        self._card_inv,  self._lbl_inv  = _stat_card(
            "Μη Έγκυρες Ημ/νίες", styles.RED)
        self._card_over, self._lbl_over = _stat_card(
            "Υπερκάλυψη Παρτίδων", styles.RED)
        self._card_exp,  self._lbl_exp  = _stat_card(
            "Ληγμένες Μονάδες", styles.RED)
        self._card_esoon, self._lbl_esoon = _stat_card(
            "Μον. που Λήγουν Σύντομα", styles.AMBER)
        sum_row2.addWidget(self._card_inv)
        sum_row2.addWidget(self._card_over)
        sum_row2.addWidget(self._card_exp)
        sum_row2.addWidget(self._card_esoon)
        self.root_layout.addLayout(sum_row2)

        note_lbl = QLabel(
            "Οι κατηγορίες μπορεί να επικαλύπτονται. Η κάλυψη δείχνει "
            "συμφωνία ποσοτήτων, όχι ότι το απόθεμα είναι κατάλληλο "
            "προς πώληση."
        )
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(
            f"color: {styles.TEXT_DIM}; font-size: 11px; padding: 0 0 4px 0;"
        )
        self.root_layout.addWidget(note_lbl)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Φίλτρο:"))
        self._filter_combo = QComboBox()
        for _key, label in FILTER_OPTIONS:
            self._filter_combo.addItem(label)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter_combo)
        filter_row.addStretch()
        self._filtered_count_lbl = QLabel("")
        self._filtered_count_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        filter_row.addWidget(self._filtered_count_lbl)
        self.root_layout.addLayout(filter_row)

        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        self._table = QTableWidget(0, len(TABLE_COLS))
        self._table.setHorizontalHeaderLabels(TABLE_COLS)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, len(TABLE_COLS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self.root_layout.addWidget(self._table, 1)

        page_row = QHBoxLayout()
        page_row.setSpacing(8)
        self._prev_btn = QPushButton("◀  Προηγούμενη")
        self._prev_btn.clicked.connect(self._prev_page)
        page_row.addWidget(self._prev_btn)
        self._page_lbl = QLabel("")
        self._page_lbl.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 12px;")
        page_row.addWidget(self._page_lbl)
        self._next_btn = QPushButton("Επόμενη  ▶")
        self._next_btn.clicked.connect(self._next_page)
        page_row.addWidget(self._next_btn)
        page_row.addStretch()
        self.root_layout.addLayout(page_row)

        self.refresh()

    @staticmethod
    def _btn_qss() -> str:
        return (
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; font-size: 13px; "
            f"font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }"
        )

    # ── Refresh (QThread-based, mirrors DashboardPage) ───────────────

    def refresh(self) -> None:
        """Start a background load.  No-op unless the page is truly IDLE."""
        if self._loading or self._thread is not None or self._worker is not None:
            return
        self._loading = True
        self._set_controls_enabled(False)
        self._show_loading_state()

        self._thread = QThread(self)
        self._worker = _StockLotIntegrityWorker(
            self._db_path,
            business_date=date.today().isoformat(),
            alert_days=self._alert_days,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)

        self._thread.start()

    def _on_refresh_clicked(self) -> None:
        self.refresh()

    def _on_alert_days_changed(self, value: int) -> None:
        if self._close_pending:
            return
        self._alert_days = value
        self._page = 1
        self.refresh()

    def _on_data_ready(self, result: StockLotIntegrityResult) -> None:
        if self._close_pending:
            return

        if not result.ok:
            self._render_error(result.error_message)
            return

        snapshot = result.snapshot

        if not snapshot.tracking.available:
            self._render_tracking_unavailable(snapshot.tracking.reason)
            self._current_snapshot = None
            self._all_products = ()
            return

        self._current_snapshot = snapshot
        self._all_products = snapshot.per_product
        self._state_lbl.hide()
        self._table.show()

        self._render_summary(snapshot)
        self._render_table()

    def _on_thread_done(self) -> None:
        """Deferred ref-drop to avoid C++ wrapper race with deleteLater.

        The ``deleteLater`` slots run during the same ``thread.finished``
        dispatch, but their DeferredDelete events are not processed until
        the next event-loop iteration.  Deferring our Python reference
        clearing by one tick ensures the C++ wrappers live long enough for
        the deleteLater machinery to reference them safely.
        """

        def _drop_refs() -> None:
            self._worker = None
            self._thread = None
            self._loading = False
            self._set_controls_enabled(True)
            if self._close_pending:
                self._close_pending = False
                self.shutdown_ready.emit()

        QTimer.singleShot(0, _drop_refs)

    # ── Shutdown (mirrors DashboardPage) ─────────────────────────────

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
        return False  # keep refs — thread still running

    # ── Control gating ───────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._refresh_btn.setEnabled(enabled)
        self._alert_spin.setEnabled(enabled)
        self._filter_combo.setEnabled(enabled)

    # ── State rendering helpers ──────────────────────────────────────

    def _show_loading_state(self) -> None:
        self._state_lbl.setText("🔄  Φόρτωση ακεραιότητας παρτίδων…")
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.hide()
        self._page_lbl.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._filtered_count_lbl.setText("")

    def _render_error(self, error_message: str) -> None:
        self._state_lbl.setText(error_message)
        self._state_lbl.setStyleSheet(
            f"color: {styles.RED}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        if self._current_snapshot is None:
            self._table.hide()
            self._page_lbl.setText("")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            self._filtered_count_lbl.setText("")
        else:
            self._table.show()
            self._render_summary(self._current_snapshot)
            self._render_table()

    def _render_tracking_unavailable(self, reason: str) -> None:
        self._state_lbl.setText(reason)
        self._state_lbl.setStyleSheet(
            f"color: {styles.AMBER}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.hide()
        self._reset_summary()
        self._page_lbl.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._filtered_count_lbl.setText("")

    def _render_empty(self) -> None:
        self._state_lbl.setText(
            "Δεν βρέθηκαν προϊόντα με θετικό απόθεμα κύριας αποθήκης.")
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.setRowCount(0)
        self._table.hide()
        self._page_lbl.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._filtered_count_lbl.setText("")

    # ── Summary ──────────────────────────────────────────────────────

    def _render_summary(self, snap: StockLotIntegritySnapshot) -> None:
        self._lbl_total.setText(str(snap.total_products_with_stock))
        self._lbl_fully.setText(str(snap.fully_covered))
        self._lbl_untr.setText(str(snap.untracked_products))
        self._lbl_undated.setText(str(snap.undated_lot_products))
        self._lbl_inv.setText(str(snap.invalid_date_products))
        self._lbl_over.setText(str(snap.lot_overage_products))
        self._lbl_exp.setText(str(snap.expired_lot_units))
        self._lbl_esoon.setText(str(snap.expiring_soon_lot_units))

    def _reset_summary(self) -> None:
        for lbl in (self._lbl_total, self._lbl_fully, self._lbl_untr,
                     self._lbl_undated, self._lbl_inv, self._lbl_over,
                     self._lbl_exp, self._lbl_esoon):
            lbl.setText("—")

    # ── Table rendering ──────────────────────────────────────────────

    def _render_table(self) -> None:
        if not self._current_snapshot or not self._all_products:
            self._render_empty()
            return

        filtered = _filter_products(self._all_products, self._filter_key)

        if not filtered:
            self._render_filtered_empty()
            return

        page_items, total_pages, clamped = _paginate(filtered, self._page)
        self._page = clamped

        self._state_lbl.hide()
        self._table.show()

        self._table.setRowCount(len(page_items))
        for r, p in enumerate(page_items):
            self._set_row(r, p)

        total_filtered = len(filtered)
        self._filtered_count_lbl.setText(
            f"Φιλτραρισμένα: {total_filtered} προϊόντα")
        self._page_lbl.setText(
            f"Σελίδα {self._page} από {total_pages}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page < total_pages)

    def _render_filtered_empty(self) -> None:
        self._state_lbl.setText(
            "Δεν βρέθηκαν προϊόντα που να ταιριάζουν με το επιλεγμένο φίλτρο.")
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.setRowCount(0)
        self._page_lbl.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._filtered_count_lbl.setText("Φιλτραρισμένα: 0 προϊόντα")

    def _set_row(self, row: int, p: ProductLotIntegrity) -> None:
        items = [
            QTableWidgetItem(p.barcode),
            QTableWidgetItem(p.product_name),
            QTableWidgetItem(str(p.master_stock)),
            QTableWidgetItem(str(p.total_lot_qty)),
            QTableWidgetItem(str(p.qty_in_dated_lots)),
            QTableWidgetItem(str(p.qty_in_undated_lots)),
            QTableWidgetItem(str(p.qty_in_invalid_date_lots)),
            QTableWidgetItem(str(p.untracked_qty)),
            QTableWidgetItem(str(p.lot_overage_qty)),
            QTableWidgetItem(
                p.earliest_valid_expiry
                if p.earliest_valid_expiry != "—"
                else "—"
            ),
            QTableWidgetItem(str(p.expired_lot_qty)),
            QTableWidgetItem(str(p.expiring_soon_lot_qty)),
            QTableWidgetItem(p.status),
        ]

        for c, item in enumerate(items):
            item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, c, item)

        self._table.item(row, 7).setToolTip(TOOLTIP_UNTRACKED)
        self._table.item(row, 8).setToolTip(TOOLTIP_OVERAGE)

        status_item = self._table.item(row, len(TABLE_COLS) - 1)
        fg = self._status_color(p)
        if fg:
            status_item.setForeground(fg)

        if p.expired_lot_qty > 0:
            self._table.item(row, 10).setForeground(QColor(styles.RED))
        if p.expiring_soon_lot_qty > 0:
            self._table.item(row, 11).setForeground(QColor(styles.AMBER))

    @staticmethod
    def _status_color(p: ProductLotIntegrity) -> QColor | None:
        if p.lot_overage_qty > 0 or p.expired_lot_qty > 0 \
                or p.qty_in_invalid_date_lots > 0:
            return QColor(styles.RED)
        if p.expiring_soon_lot_qty > 0 or p.untracked_qty > 0 \
                or p.qty_in_undated_lots > 0:
            return QColor(styles.AMBER)
        return None

    # ── Filter / pagination ─────────────────────────────────────────

    def _on_filter_changed(self, idx: int) -> None:
        if self._close_pending:
            return
        key = FILTER_KEYS[idx] if 0 <= idx < len(FILTER_KEYS) else "all"
        self._filter_key = key
        self._page = 1
        self._render_table()

    def _prev_page(self) -> None:
        if self._loading or self._close_pending:
            return
        if self._page > 1:
            self._page -= 1
            self._render_table()

    def _next_page(self) -> None:
        if self._loading or self._close_pending:
            return
        self._page += 1
        self._render_table()
