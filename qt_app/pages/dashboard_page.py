"""Dashboard page — στατιστικά επισκόπησης με πραγματικά δεδομένα."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    fetch_dashboard_counts,
    fetch_critical_products,
    fetch_dashboard_analytics,
)


# ── Helpers ─────────────────────────────────────────────────────────────

def _stat_card(title: str, value: str, accent: str = styles.GREEN) -> QFrame:
    """Build a bordered stat card (title + large value)."""
    card = QFrame()
    card.setFrameShape(QFrame.StyledPanel)
    card.setStyleSheet(
        f"QFrame {{ background: {styles.DARK_SURFACE}; border-radius: 8px; "
        f"border: 1px solid {styles.BORDER}; padding: 16px; }}")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    card.setMinimumHeight(90)

    lay = QHBoxLayout(card)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    inner = QHBoxLayout()
    inner.setSpacing(0)
    inner.addStretch()

    # Value
    val_lbl = QLabel(value)
    val_lbl.setFont(QFont("Segoe UI", 28, QFont.Bold))
    val_lbl.setStyleSheet(f"color: {accent};")
    inner.addWidget(val_lbl)
    inner.addStretch()
    lay.addLayout(inner)

    # Title (below value via a nested VBox approach — actually simpler
    # to use two rows in a QVBoxLayout but keeping it compact)
    return card


class DashboardPage(BasePage):
    """System overview with stat cards and critical alerts."""

    @classmethod
    def page_title(cls) -> str:
        return "Επισκόπηση Συστήματος"

    def __init__(self, db_service, config: dict, parent=None):
        # Resolve DB path from config, fall back to project-root default
        db_path = config.get("db_path", "encomm_erp.db") if config else "encomm_erp.db"
        self._db_path = db_path
        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────
    def build_ui(self) -> None:
        """Create stat cards, analytics row, and critical alerts table."""
        # Row 1: 3 stat cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self._card_total = _stat_card("Συνολικά Προϊόντα", "—")
        self._card_low = _stat_card("Ελλείψεις Στοκ", "—", styles.AMBER)
        self._card_expiry = _stat_card("Κοντά στη Λήξη / Ληγμένα", "—", styles.RED)

        cards_row.addWidget(self._card_total)
        cards_row.addWidget(self._card_low)
        cards_row.addWidget(self._card_expiry)
        self.root_layout.addLayout(cards_row)

        # Row 2: 3 analytics cards (revenue, VAT, invoice count)
        an_row = QHBoxLayout()
        an_row.setSpacing(12)

        self._card_rev = _stat_card("Έσοδα Σήμερα", "—", styles.GREEN)
        self._card_vat = _stat_card("ΦΠΑ Σήμερα", "—", styles.AMBER)
        self._card_inv = _stat_card("Παραστατικά", "—")

        an_row.addWidget(self._card_rev)
        an_row.addWidget(self._card_vat)
        an_row.addWidget(self._card_inv)
        self.root_layout.addLayout(an_row)

        # Table: critical products
        tbl_lbl = QLabel("⚠️  Κρίσιμα Προϊόντα (Χαμηλό Στοκ ή Κοντά στη Λήξη)")
        tbl_lbl.setFont(QFont("Segoe UI", 13, QFont.Bold))
        tbl_lbl.setStyleSheet(f"color: {styles.TEXT_PRIMARY};")
        self.root_layout.addWidget(tbl_lbl)

        self._alerts_table = QTableWidget(0, 4)
        self._alerts_table.setHorizontalHeaderLabels(
            ["Όνομα Προϊόντος", "Στοκ", "Ημ. Λήξης", "Αιτία Προειδοποίησης"])
        self._alerts_table.horizontalHeader().setStretchLastSection(True)
        self._alerts_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._alerts_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self._alerts_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)
        self._alerts_table.verticalHeader().setVisible(False)
        self._alerts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._alerts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.root_layout.addWidget(self._alerts_table, 1)

        self._built = True
        self.refresh()

    # ── Data refresh ─────────────────────────────────────────────────
    def refresh(self) -> None:
        """Load counts, analytics, and critical products from the DB."""
        threshold = int(self.config.get("low_stock_threshold", 10)) if self.config else 10
        alert_days = int(self.config.get("expiry_alert_days", 30)) if self.config else 30

        counts = fetch_dashboard_counts(self._db_path, threshold, alert_days)
        self._update_card_value(self._card_total, str(counts["total"]))
        self._update_card_value(self._card_low, str(counts["low_stock"]))
        self._update_card_value(self._card_expiry, str(counts["expiry"]))

        analytics = fetch_dashboard_analytics(self._db_path)
        self._update_card_value(
            self._card_rev, f"€{analytics.get('revenue_today', 0):.2f}")
        self._update_card_value(
            self._card_vat, f"€{analytics.get('vat_today', 0):.2f}")
        self._update_card_value(
            self._card_inv, str(analytics.get("invoice_count", 0)))

        # Critical alerts table
        items = fetch_critical_products(
            self._db_path, threshold, alert_days, limit=20)
        self._alerts_table.setRowCount(len(items))
        for r, (name, stock, expiry, reason) in enumerate(items):
            self._alerts_table.setItem(r, 0, QTableWidgetItem(name))
            self._alerts_table.setItem(
                r, 1, QTableWidgetItem(f"{stock} τεμ."))
            self._alerts_table.setItem(r, 2, QTableWidgetItem(expiry))
            self._alerts_table.setItem(r, 3, QTableWidgetItem(reason))
            # Colour rows by severity
            if "Ληγμένο" in reason:
                for c in range(4):
                    self._alerts_table.item(r, c).setForeground(
                        QColor(styles.RED))
            elif "Λήγει" in reason:
                for c in range(4):
                    self._alerts_table.item(r, c).setForeground(
                        QColor(styles.AMBER))

    @staticmethod
    def _update_card_value(card: QFrame, text: str) -> None:
        """Find the value QLabel inside a stat card and update its text."""
        # The card layout is: card → HBox → HBox → (stretch, QLabel, stretch)
        inner = card.layout().itemAt(0).layout()
        for i in range(inner.count()):
            w = inner.itemAt(i).widget()
            if isinstance(w, QLabel):
                w.setText(text)
                return
