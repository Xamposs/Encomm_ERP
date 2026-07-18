"""Dashboard page — στατιστικά επισκόπησης και Κέντρο Ημερήσιων Ειδοποιήσεων.

Uses a QObject worker on a QThread for all SQLite I/O.  The worker loads
both ``load_dashboard()`` and ``load_daily_alerts()`` in one run.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QComboBox,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_dashboard, DashboardResult, DashboardSnapshot,
    load_daily_alerts, DailyAlertsResult, DailyAlertsSnapshot,
)

# ── Daily alerts filter labels ─────────────────────────────────────────
ALERT_FILTERS = [
    ("all",            "Όλες οι ειδοποιήσεις"),
    ("expired",        "Ληγμένα"),
    ("expiring_soon",  "Λήγουν σύντομα"),
    ("low_stock",      "Χαμηλό απόθεμα"),
]
ALERT_FILTER_KEYS = [f[0] for f in ALERT_FILTERS]


# ═══════════════════════════════════════════════════════════════════════
# Combined result — carries both Dashboard + Daily Alerts snapshots
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class _DashboardCombined:
    dash_ok: bool
    dash_snapshot: DashboardSnapshot | None
    dash_error: str
    alerts_ok: bool
    alerts_snapshot: DailyAlertsSnapshot | None
    alerts_error: str


# ═══════════════════════════════════════════════════════════════════════
# QThread worker
# ═══════════════════════════════════════════════════════════════════════

class _DashboardWorker(QObject):
    finished = Signal(_DashboardCombined)

    def __init__(self, db_path: str, threshold: int, alert_days: int,
                 alert_filter: str, alert_page: int, alert_page_size: int,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._db = db_path
        self._threshold = threshold
        self._alert_days = alert_days
        self._alert_filter = alert_filter
        self._alert_page = alert_page
        self._alert_page_size = alert_page_size

    def run(self) -> None:
        dr = load_dashboard(self._db, self._threshold, self._alert_days)
        ar = load_daily_alerts(
            self._db, self._alert_filter,
            self._threshold, self._alert_days,
            self._alert_page, self._alert_page_size,
        )
        self.finished.emit(_DashboardCombined(
            dash_ok=dr.ok,
            dash_snapshot=dr.snapshot,
            dash_error=dr.error_message,
            alerts_ok=ar.ok,
            alerts_snapshot=ar.snapshot,
            alerts_error=ar.error_message,
        ))


# ═══════════════════════════════════════════════════════════════════════
# Stat-card builder
# ═══════════════════════════════════════════════════════════════════════

def _stat_card(title: str, accent: str = styles.GREEN
               ) -> tuple[QFrame, QLabel]:
    card = QFrame()
    card.setFrameShape(QFrame.StyledPanel)
    card.setStyleSheet(
        f"QFrame {{ background: {styles.DARK_SURFACE}; "
        f"border-radius: 8px; border: 1px solid {styles.BORDER}; "
        f"padding: 14px; }}")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    title_lbl = QLabel(title)
    title_lbl.setFont(QFont("Segoe UI", 11))
    title_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED};")
    lay.addWidget(title_lbl)
    value_lbl = QLabel("—")
    value_lbl.setFont(QFont("Segoe UI", 28, QFont.Bold))
    value_lbl.setStyleSheet(f"color: {accent};")
    lay.addWidget(value_lbl)
    return card, value_lbl


# ── Alert-count badge ────────────────────────────────────────────────────

def _alert_badge(label: str, accent: str) -> tuple[QFrame, QLabel]:
    badge = QFrame()
    badge.setStyleSheet(
        f"QFrame {{ background: {styles.DARK_SURFACE}; "
        f"border-radius: 6px; border: 1px solid {styles.BORDER}; "
        f"padding: 8px 12px; }}")
    lay = QHBoxLayout(badge)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    title_lbl = QLabel(label)
    title_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 12px;")
    lay.addWidget(title_lbl)
    count_lbl = QLabel("—")
    count_lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
    count_lbl.setStyleSheet(f"color: {accent};")
    lay.addWidget(count_lbl)
    lay.addStretch()
    return badge, count_lbl


# ═══════════════════════════════════════════════════════════════════════
# Dashboard page
# ═══════════════════════════════════════════════════════════════════════

class DashboardPage(BasePage):
    """System overview with stat cards + Κέντρο Ημερήσιων Ειδοποιήσεων."""

    shutdown_ready = Signal()

    ALERT_COLS = [
        "Barcode", "Όνομα Προϊόντος", "Στοκ",
        "Ημ. Λήξης", "Τιμή", "Αιτία",
    ]

    @classmethod
    def page_title(cls) -> str:
        return "Επισκόπηση Συστήματος"

    def __init__(self, db_service, config: dict, parent=None):
        db_path = (config.get("db_path", "encomm_erp.db")
                   if config else "encomm_erp.db")
        self._db_path = db_path
        self._worker: _DashboardWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False
        self._alert_filter = "all"
        self._alert_page = 1
        self._alert_page_size = 20
        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────
    def build_ui(self) -> None:
        # ── Row 1 — 3 stat cards ──────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._card_total, self._lbl_total = _stat_card("Συνολικά Προϊόντα")
        self._card_low,   self._lbl_low   = _stat_card(
            "Ελλείψεις Στοκ", styles.AMBER)
        self._card_expiry, self._lbl_expiry = _stat_card(
            "Κοντά στη Λήξη / Ληγμένα", styles.RED)
        cards_row.addWidget(self._card_total)
        cards_row.addWidget(self._card_low)
        cards_row.addWidget(self._card_expiry)
        self.root_layout.addLayout(cards_row)

        # ── Row 2 — 3 analytics cards ──────────────────────────────────
        an_row = QHBoxLayout()
        an_row.setSpacing(12)
        self._card_rev, self._lbl_rev = _stat_card("Έσοδα Σήμερα", styles.GREEN)
        self._card_vat, self._lbl_vat = _stat_card("ΦΠΑ Σήμερα", styles.AMBER)
        self._card_inv, self._lbl_inv = _stat_card("Παραστατικά")
        an_row.addWidget(self._card_rev)
        an_row.addWidget(self._card_vat)
        an_row.addWidget(self._card_inv)
        self.root_layout.addLayout(an_row)

        # ── Refresh button ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 20px; "
            f"font-size: 13px; font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")
        self._refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        self.root_layout.addLayout(btn_row)

        # ── Κέντρο Ημερήσιων Ειδοποιήσεων section header ──────────────
        section_lbl = QLabel('📋  Κέντρο Ημερήσιων Ειδοποιήσεων')
        section_lbl.setFont(QFont("Segoe UI", 15, QFont.Bold))
        section_lbl.setStyleSheet(f"color: {styles.TEXT_PRIMARY};")
        self.root_layout.addWidget(section_lbl)

        # ── 3 alert-count badges ───────────────────────────────────────
        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)
        self._badge_low,   self._lbl_alert_low   = _alert_badge(
            "Χαμηλό απόθεμα", styles.AMBER)
        self._badge_near,  self._lbl_alert_near  = _alert_badge(
            "Λήγουν σύντομα", styles.AMBER)
        self._badge_exp,   self._lbl_alert_exp   = _alert_badge(
            "Ληγμένα", styles.RED)
        badges_row.addWidget(self._badge_low)
        badges_row.addWidget(self._badge_near)
        badges_row.addWidget(self._badge_exp)
        badges_row.addStretch()
        self.root_layout.addLayout(badges_row)

        # ── Filter + page label ────────────────────────────────────────
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self._filter_combo = QComboBox()
        for _key, label in ALERT_FILTERS:
            self._filter_combo.addItem(label)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        ctrl_row.addWidget(self._filter_combo)
        ctrl_row.addStretch()
        self.root_layout.addLayout(ctrl_row)

        # ── State label ────────────────────────────────────────────────
        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        # ── Alerts table ────────────────────────────────────────────────
        self._alerts_table = QTableWidget(0, len(self.ALERT_COLS))
        self._alerts_table.setHorizontalHeaderLabels(self.ALERT_COLS)
        hdr = self._alerts_table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._alerts_table.verticalHeader().setVisible(False)
        self._alerts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._alerts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._alerts_table.itemSelectionChanged.connect(
            self._on_alert_selection_changed)
        self._alerts_table.cellDoubleClicked.connect(
            self._on_alert_double_click)
        self.root_layout.addWidget(self._alerts_table, 1)

        # ── Bottom row: open-in-inventory button + pagination ──────────
        bot_row = QHBoxLayout()
        bot_row.setSpacing(8)
        self._open_inv_btn = QPushButton("📦  Άνοιγμα στην Αποθήκη")
        self._open_inv_btn.setCursor(Qt.PointingHandCursor)
        self._open_inv_btn.setStyleSheet(
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; "
            f"font-size: 12px; font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")
        self._open_inv_btn.setEnabled(False)
        self._open_inv_btn.clicked.connect(self._on_open_in_inventory)
        bot_row.addWidget(self._open_inv_btn)
        bot_row.addStretch()
        self._prev_btn = QPushButton("◀  Προηγούμενη")
        self._prev_btn.clicked.connect(self._prev_page)
        bot_row.addWidget(self._prev_btn)
        self._page_lbl = QLabel("")
        self._page_lbl.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 12px;")
        bot_row.addWidget(self._page_lbl)
        self._next_btn = QPushButton("Επόμενη  ▶")
        self._next_btn.clicked.connect(self._next_page)
        bot_row.addWidget(self._next_btn)
        self.root_layout.addLayout(bot_row)

        self._built = True
        self.refresh()

    # ── Refresh (QThread-based) ─────────────────────────────────────

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση δεδομένων...", styles.TEXT_MUTED)

        threshold = int(self.config.get("low_stock_threshold", 10)
                        ) if self.config else 10
        alert_days = int(self.config.get("expiry_alert_days", 30)
                         ) if self.config else 30

        self._cleanup_worker()

        self._thread = QThread(self)
        self._worker = _DashboardWorker(
            self._db_path, threshold, alert_days,
            self._alert_filter, self._alert_page, self._alert_page_size)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)

        self._thread.start()

    def _on_data_ready(self, combined: _DashboardCombined) -> None:
        if self._close_pending:
            return

        # ── Dashboard stats ──────────────────────────────────────────
        if not combined.dash_ok:
            self._set_state(combined.dash_error, styles.RED)
            self._alerts_table.setRowCount(0)
            self._reset_values_dash()
            self._reset_alert_badges()
            self._page_lbl.setText("")
            return
        ds = combined.dash_snapshot
        self._lbl_total.setText(str(ds.total_products))
        self._lbl_low.setText(str(ds.low_stock_count))
        self._lbl_expiry.setText(str(ds.expiry_alert_count))
        self._lbl_rev.setText(f"€{ds.revenue_today:.2f}")
        self._lbl_vat.setText(f"€{ds.vat_today:.2f}")
        self._lbl_inv.setText(str(ds.invoice_count))

        # ── Daily alerts ─────────────────────────────────────────────
        if not combined.alerts_ok:
            self._alerts_table.setRowCount(0)
            self._reset_alert_badges()
            self._page_lbl.setText("")
            # Don't clobber the dashboard stats; show error below
            self._set_state(combined.alerts_error, styles.RED)
            return

        snap = combined.alerts_snapshot
        self._lbl_alert_low.setText(str(snap.low_stock_count))
        self._lbl_alert_near.setText(str(snap.expiring_soon_count))
        self._lbl_alert_exp.setText(str(snap.expired_count))

        items = snap.items
        if snap.total_alerts == 0:
            self._set_state("✅ Καμία ειδοποίηση.", styles.GREEN)
            self._alerts_table.setRowCount(0)
            self._page_lbl.setText("")
            return

        self._state_lbl.hide()
        self._alerts_table.show()
        self._alerts_table.setRowCount(len(items))
        for r, item in enumerate(items):
            reason_text = " · ".join(item.reasons)
            self._alerts_table.setItem(r, 0, QTableWidgetItem(item.barcode))
            self._alerts_table.setItem(r, 1, QTableWidgetItem(item.name))
            self._alerts_table.setItem(r, 2, QTableWidgetItem(str(item.stock)))
            self._alerts_table.setItem(r, 3, QTableWidgetItem(item.expiry_date))
            self._alerts_table.setItem(r, 4, QTableWidgetItem(f"€{item.price:.2f}"))
            self._alerts_table.setItem(r, 5, QTableWidgetItem(reason_text))
            fg = QColor(styles.RED) if any(
                "Ληγμένο" in s for s in item.reasons
            ) else QColor(styles.AMBER)
            for c in range(len(self.ALERT_COLS)):
                self._alerts_table.item(r, c).setForeground(fg)

        total_pages = max(1, (snap.total_alerts + snap.page_size - 1)
                          // snap.page_size)
        self._page_lbl.setText(
            f"Σελίδα {snap.page} από {total_pages}  "
            f"({snap.total_alerts} ειδοποιήσεις)")
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

    # ── Filter / pagination ──────────────────────────────────────────

    def _on_filter_changed(self, idx: int) -> None:
        self._alert_filter = ALERT_FILTER_KEYS[idx] if 0 <= idx < len(ALERT_FILTER_KEYS) else "all"
        self._alert_page = 1
        self.refresh()

    def _prev_page(self) -> None:
        if self._alert_page > 1:
            self._alert_page -= 1
            self.refresh()

    def _next_page(self) -> None:
        self._alert_page += 1
        self.refresh()

    # ── Selection / navigation ───────────────────────────────────────

    def _on_alert_selection_changed(self) -> None:
        rows = set()
        for item in self._alerts_table.selectedItems():
            rows.add(item.row())
        self._open_inv_btn.setEnabled(len(rows) == 1)

    def _on_alert_double_click(self, row: int, _col: int) -> None:
        self._open_barcode_from_row(row)

    def _on_open_in_inventory(self) -> None:
        rows = set()
        for item in self._alerts_table.selectedItems():
            rows.add(item.row())
        if len(rows) != 1:
            return
        self._open_barcode_from_row(list(rows)[0])

    def _open_barcode_from_row(self, row: int) -> None:
        barcode_item = self._alerts_table.item(row, 0)
        if barcode_item is None:
            return
        barcode = barcode_item.text()
        mw = self.window()
        if mw and hasattr(mw, "open_inventory_with_barcode"):
            mw.open_inventory_with_barcode(barcode)

    # ── Helpers ─────────────────────────────────────────────────────

    def _set_state(self, text: str, color: str) -> None:
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(
            f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._alerts_table.hide()

    def _reset_values_dash(self) -> None:
        for lbl in (self._lbl_total, self._lbl_low, self._lbl_expiry,
                     self._lbl_rev, self._lbl_vat, self._lbl_inv):
            lbl.setText("—")

    def _reset_alert_badges(self) -> None:
        for lbl in (self._lbl_alert_low, self._lbl_alert_near,
                     self._lbl_alert_exp):
            lbl.setText("—")
