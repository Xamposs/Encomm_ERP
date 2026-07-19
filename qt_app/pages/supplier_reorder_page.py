"""Supplier Reorder Candidates — read-only UI (P3.2).

Displays low-stock products grouped by supplier, plus an unassigned
section for products without a valid supplier.  Data comes exclusively
from :func:`qt_app.data_source.load_supplier_reorder_candidates` — no
SQL, no writes, no persistence.
"""

from __future__ import annotations

import sqlite3
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QVBoxLayout, QWidget, QScrollArea,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_supplier_reorder_candidates,
    SupplierReorderResult, SupplierReorderGroup,
    UnassignedReorderProduct, ReorderCandidate,
)


# ── Worker ──────────────────────────────────────────────────────────────

class _ReorderWorker(QObject):
    finished = Signal(SupplierReorderResult)

    def __init__(self, db_path: str, threshold: int, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._threshold = threshold

    def run(self) -> None:
        self.finished.emit(
            load_supplier_reorder_candidates(self._db_path, self._threshold))


# ── Page ────────────────────────────────────────────────────────────────

class SupplierReorderPage(BasePage):
    """Read-only view of supplier reorder candidates (P3.2)."""

    shutdown_ready = Signal()

    # Columns for grouped (assigned) products
    _GROUPED_COLS = [
        "Barcode", "Προϊόν", "Απόθεμα", "Όριο", "Λήξη", "Τιμή",
    ]
    # Columns for unassigned products — one extra for the reason
    _UNASSIGNED_COLS = [
        "Barcode", "Προϊόν", "Απόθεμα", "Όριο", "Λήξη", "Τιμή", "Αιτία",
    ]

    def __init__(self, db_service, config, parent=None):
        self._db_path = (
            config.get("db_path", "encomm_erp.db")
            if config else "encomm_erp.db"
        )
        self._worker: _ReorderWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False
        self._threshold = 10
        super().__init__(db_service, config, parent)

    # ── UI construction ───────────────────────────────────────────────

    def build_ui(self) -> None:
        # ── Toolbar ───────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(8)

        tb.addWidget(QLabel("Όριο αναπαραγγελίας:"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(1, 10000)
        self._threshold_spin.setValue(self._threshold)
        self._threshold_spin.setMinimumHeight(36)
        tb.addWidget(self._threshold_spin)
        tb.addStretch()

        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(self._refresh_btn)
        self.root_layout.addLayout(tb)

        # ── Summary ──────────────────────────────────────────────────
        self._summary = QLabel("")
        self._summary.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        self.root_layout.addWidget(self._summary)

        # ── State label (loading / empty / error) ─────────────────────
        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        self.root_layout.addWidget(self._state_lbl)

        # ── Scrollable content area ───────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {styles.DARK_BG}; }}")
        self._scroll_widget = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(12)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_widget)
        self.root_layout.addWidget(self._scroll, 1)

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

    # ── Refresh / worker lifecycle ────────────────────────────────────

    def refresh(self) -> None:
        if self._loading:
            return
        self._threshold = self._threshold_spin.value()
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση υποψηφίων αναπαραγγελίας...",
                        styles.TEXT_MUTED)
        self._summary.setText("")
        self._clear_content()

        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _ReorderWorker(self._db_path, self._threshold)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_data_ready(self, result: SupplierReorderResult) -> None:
        if self._close_pending:
            return
        if not result.ok:
            self._set_state(result.error_message, styles.RED)
            self._summary.setText("")
            self._clear_content()
            return

        groups = result.groups
        unassigned = result.unassigned

        if not groups and not unassigned:
            self._set_state(
                "Δεν βρέθηκαν προϊόντα κάτω από το όριο αναπαραγγελίας.",
                styles.TEXT_MUTED)
            self._summary.setText("")
            self._clear_content()
            return

        total_products = sum(len(g.products) for g in groups) + len(unassigned)
        total_suppliers = len(groups)
        self._summary.setText(
            f"Σύνολο: {total_products} προϊόντα "
            f"σε {total_suppliers} προμηθευτές"
            + (f"  |  Αταξινόμητα: {len(unassigned)}"
               if unassigned else ""))

        self._state_lbl.hide()

        # ── Build grouped supplier sections ──────────────────────────
        for group in groups:
            gb = QGroupBox(f"Προμηθευτής: {group.supplier_name}")
            gb.setStyleSheet(self._group_box_qss())
            gb_layout = QVBoxLayout(gb)
            gb_layout.setContentsMargins(8, 20, 8, 8)

            table = self._build_product_table(
                self._GROUPED_COLS, group.products,
                extra_cols=0)
            gb_layout.addWidget(table)
            self._scroll_layout.insertWidget(
                self._scroll_layout.count() - 1, gb)

        # ── Build unassigned section ─────────────────────────────────
        if unassigned:
            gb = QGroupBox("Αταξινόμητα Προϊόντα")
            gb.setStyleSheet(self._group_box_qss())
            gb_layout = QVBoxLayout(gb)
            gb_layout.setContentsMargins(8, 20, 8, 8)

            table = QTableWidget(0, len(self._UNASSIGNED_COLS))
            table.setHorizontalHeaderLabels(self._UNASSIGNED_COLS)
            h = table.horizontalHeader()
            h.setStretchLastSection(True)
            h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            h.setSectionResizeMode(1, QHeaderView.Stretch)
            for i in range(2, len(self._UNASSIGNED_COLS)):
                h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setEditTriggers(QTableWidget.NoEditTriggers)

            table.setRowCount(len(unassigned))
            for r, up in enumerate(unassigned):
                self._populate_row(table, r, up.barcode, up.name,
                                   up.stock, up.threshold,
                                   up.expiry_date, up.price,
                                   extra_values=[up.reason])
            gb_layout.addWidget(table)
            self._scroll_layout.insertWidget(
                self._scroll_layout.count() - 1, gb)

    def _build_product_table(
        self,
        cols: list[str],
        products: tuple[ReorderCandidate, ...] | tuple[UnassignedReorderProduct, ...],
        extra_cols: int = 0,
    ) -> QTableWidget:
        """Build a QTableWidget populated with reorder candidate rows."""
        table = QTableWidget(0, len(cols))
        table.setHorizontalHeaderLabels(cols)
        h = table.horizontalHeader()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, len(cols)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        table.setRowCount(len(products))
        for r, p in enumerate(products):
            self._populate_row(table, r, p.barcode, p.name,
                               p.stock, p.threshold,
                               p.expiry_date, p.price)
        return table

    @staticmethod
    def _populate_row(
        table: QTableWidget,
        row: int,
        barcode: str,
        name: str,
        stock: int,
        threshold: int,
        expiry_date: str,
        price: float,
        extra_values: list[str] | None = None,
    ) -> None:
        """Fill one row of a reorder table with standard candidate columns."""
        table.setItem(row, 0, QTableWidgetItem(barcode))
        table.setItem(row, 1, QTableWidgetItem(name))
        table.setItem(row, 2, QTableWidgetItem(str(stock)))
        table.setItem(row, 3, QTableWidgetItem(str(threshold)))
        table.setItem(row, 4, QTableWidgetItem(expiry_date))
        table.setItem(row, 5, QTableWidgetItem(f"€{price:.2f}"))
        if extra_values:
            for i, val in enumerate(extra_values):
                table.setItem(row, 6 + i, QTableWidgetItem(val))

    def _on_thread_done(self) -> None:
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()

    # ── Cleanup / shutdown ────────────────────────────────────────────

    def _cleanup_worker(self) -> None:
        if self._thread and self._thread.isRunning():
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
            self._worker = None
            self._thread = None
            self._loading = False
            self._close_pending = False
            return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────

    def _set_state(self, text: str, color: str) -> None:
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(
            f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()

    def _clear_content(self) -> None:
        """Remove all supplier/unassigned group boxes from the scroll area."""
        while self._scroll_layout.count() > 1:
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @staticmethod
    def _group_box_qss() -> str:
        return (
            f"QGroupBox {{ "
            f"border: 1px solid {styles.BORDER}; "
            f"border-radius: 8px; "
            f"margin-top: 16px; "
            f"padding: 16px; "
            f"color: {styles.TEXT_PRIMARY}; "
            f"font-size: 13px; "
            f"font-weight: bold; }}"
            f"QGroupBox::title {{ "
            f"subcontrol-origin: margin; "
            f"left: 12px; "
            f"padding: 0 6px; "
            f"color: {styles.ACCENT}; }}"
        )
