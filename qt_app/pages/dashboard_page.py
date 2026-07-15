"""Dashboard page — στατιστικά επισκόπησης με πραγματικά δεδομένα.

Uses a QObject worker on a QThread for all SQLite I/O.  The UI thread
updates widgets only when the worker signals completion.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy, QSpacerItem,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_dashboard, DashboardResult, DashboardSnapshot,
)


# ═══════════════════════════════════════════════════════════════════════
# QThread worker
# ═══════════════════════════════════════════════════════════════════════

class _DashboardWorker(QObject):
    """Runs ``load_dashboard`` on a background thread and emits the result."""

    finished = Signal(DashboardResult)

    def __init__(self, db_path: str, threshold: int, alert_days: int,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._threshold = threshold
        self._alert_days = alert_days

    def run(self) -> None:
        result = load_dashboard(
            self._db_path, self._threshold, self._alert_days)
        self.finished.emit(result)


# ═══════════════════════════════════════════════════════════════════════
# Stat-card builder
# ═══════════════════════════════════════════════════════════════════════

def _stat_card(title: str, accent: str = styles.GREEN
               ) -> tuple[QFrame, QLabel]:
    """Return ``(card_frame, value_label)``.

    The card uses a QVBoxLayout: title QLabel at top (subtle, 11 px),
    value QLabel below (28 px bold, accent colour).
    """
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

    # Title
    title_lbl = QLabel(title)
    title_lbl.setFont(QFont("Segoe UI", 11))
    title_lbl.setStyleSheet(f"color: {styles.TEXT_MUTED};")
    lay.addWidget(title_lbl)

    # Value
    value_lbl = QLabel("—")
    value_lbl.setFont(QFont("Segoe UI", 28, QFont.Bold))
    value_lbl.setStyleSheet(f"color: {accent};")
    lay.addWidget(value_lbl)

    return card, value_lbl


# ═══════════════════════════════════════════════════════════════════════
# Dashboard page
# ═══════════════════════════════════════════════════════════════════════

class DashboardPage(BasePage):
    """System overview with real stat cards and critical alerts."""

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
        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────
    def build_ui(self) -> None:
        """Create stat cards, analytics row, refresh button, state labels,
        and the 6-column critical-alerts table."""

        # Row 1 — 3 stat cards (direct value-label references stored)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self._card_total, self._lbl_total = _stat_card(
            "Συνολικά Προϊόντα")
        self._card_low,   self._lbl_low   = _stat_card(
            "Ελλείψεις Στοκ", styles.AMBER)
        self._card_expiry, self._lbl_expiry = _stat_card(
            "Κοντά στη Λήξη / Ληγμένα", styles.RED)

        cards_row.addWidget(self._card_total)
        cards_row.addWidget(self._card_low)
        cards_row.addWidget(self._card_expiry)
        self.root_layout.addLayout(cards_row)

        # Row 2 — 3 analytics cards
        an_row = QHBoxLayout()
        an_row.setSpacing(12)

        self._card_rev, self._lbl_rev = _stat_card(
            "Έσοδα Σήμερα", styles.GREEN)
        self._card_vat, self._lbl_vat = _stat_card(
            "ΦΠΑ Σήμερα", styles.AMBER)
        self._card_inv, self._lbl_inv = _stat_card("Παραστατικά")

        an_row.addWidget(self._card_rev)
        an_row.addWidget(self._card_vat)
        an_row.addWidget(self._card_inv)
        self.root_layout.addLayout(an_row)

        # Refresh button row
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

        # State label (loading / error / empty — stacked below the
        # table via the same layout slot)
        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        # Critical alerts table (6 columns)
        self._alerts_table = QTableWidget(0, 6)
        self._alerts_table.setHorizontalHeaderLabels([
            "Barcode", "Όνομα Προϊόντος", "Στοκ",
            "Ημ. Λήξης", "Τιμή", "Αιτία Προειδοποίησης"])
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
        self.root_layout.addWidget(self._alerts_table, 1)

        self._built = True
        self.refresh()

    # ── Refresh (QThread-based) ─────────────────────────────────────
    def refresh(self) -> None:
        """Start a background worker to load dashboard data."""
        if self._loading:
            return  # prevent overlapping refreshes
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση δεδομένων dashboard...", styles.TEXT_MUTED)

        threshold = int(self.config.get("low_stock_threshold", 10)
                        ) if self.config else 10
        alert_days = int(self.config.get("expiry_alert_days", 30)
                         ) if self.config else 30

        # Clean up any previous thread/worker
        self._cleanup_worker()

        self._thread = QThread(self)
        self._worker = _DashboardWorker(
            self._db_path, threshold, alert_days)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)

        self._thread.start()

    def _on_data_ready(self, result: DashboardResult) -> None:
        """Handle the worker's result (runs on UI thread via signal)."""
        if not result.ok:
            self._set_state(result.error_message, styles.RED)
            self._clear_table()
            self._reset_values_dash()
            return

        snap = result.snapshot
        self._lbl_total.setText(str(snap.total_products))
        self._lbl_low.setText(str(snap.low_stock_count))
        self._lbl_expiry.setText(str(snap.expiry_alert_count))
        self._lbl_rev.setText(f"€{snap.revenue_today:.2f}")
        self._lbl_vat.setText(f"€{snap.vat_today:.2f}")
        self._lbl_inv.setText(str(snap.invoice_count))

        crit = snap.critical_products
        if not crit:
            self._set_state("✅ Κανένα κρίσιμο προϊόν.", styles.GREEN)
            self._clear_table()
        else:
            self._state_lbl.hide()
            self._alerts_table.show()
            self._alerts_table.setRowCount(len(crit))
            for r, cp in enumerate(crit):
                reason_text = " · ".join(cp.reasons)
                self._alerts_table.setItem(r, 0, QTableWidgetItem(cp.barcode))
                self._alerts_table.setItem(r, 1, QTableWidgetItem(cp.name))
                self._alerts_table.setItem(
                    r, 2, QTableWidgetItem(str(cp.stock)))
                self._alerts_table.setItem(
                    r, 3, QTableWidgetItem(cp.expiry_date))
                self._alerts_table.setItem(
                    r, 4, QTableWidgetItem(f"€{cp.price:.2f}"))
                self._alerts_table.setItem(
                    r, 5, QTableWidgetItem(reason_text))
                # Colour rows
                fg = QColor(styles.RED) if any(
                    "Ληγμένο" in rsn for rsn in cp.reasons
                ) else QColor(styles.AMBER)
                for c in range(6):
                    self._alerts_table.item(r, c).setForeground(fg)

    def _on_thread_done(self) -> None:
        """Clean up after the thread finishes."""
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._worker = None
        self._thread = None

    def _cleanup_worker(self) -> None:
        """Safely tear down any existing worker thread."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    # ── Helpers ─────────────────────────────────────────────────────
    def _set_state(self, text: str, color: str) -> None:
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(
            f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._alerts_table.hide()

    def _clear_table(self) -> None:
        self._alerts_table.setRowCount(0)

    def _reset_values_dash(self) -> None:
        """Set all value labels to dash on error."""
        for lbl in (self._lbl_total, self._lbl_low, self._lbl_expiry,
                     self._lbl_rev, self._lbl_vat, self._lbl_inv):
            lbl.setText("—")
