"""Goods Receipts page — supplier stock intake with approval control."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QCheckBox, QStackedWidget,
    QWidget, QFrame, QFormLayout, QSpinBox, QDoubleSpinBox,
    QDateEdit, QSplitter, QGroupBox, QSizePolicy, QScrollArea,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from infrastructure.goods_receipt_service import (
    list_receipts, get_receipt,
    create_receipt_draft, approve_receipt, cancel_receipt,
    ReceiptListResult, GetReceiptResult,
    CreateDraftResult, ApproveReceiptResult, CancelReceiptResult,
)

# ── Lightweight supplier loader (not importing data_source to keep deps minimal) ──

def _load_suppliers_for_combo(db_path: str) -> list[tuple[int, str]]:
    """Return [(id, name), ...] for all suppliers, ordered by name."""
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, name FROM suppliers ORDER BY name ASC")
        return [(r["id"], r["name"]) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


DOC_TYPE_LABELS = {
    "delivery_note": "Δελτίο Αποστολής",
    "supplier_invoice": "Τιμολόγιο Προμηθευτή",
}

STATUS_LABELS = {
    "draft": "Πρόχειρο",
    "approved": "Εγκεκριμένο",
    "cancelled": "Ακυρωμένο",
}

# ── Constants for receipt styles ────────────────────────────────────
STATUS_COLORS = {
    "draft": "#F59E0B",
    "approved": "#10B981",
    "cancelled": "#EF4444",
}


class _ReceiptWorker(QObject):
    """Single worker for all goods-receipt operations.  Mode dispatches result."""

    finished = Signal(object)  # typed result — page dispatches by mode

    def __init__(self, db_path: str, mode: str, args: dict, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._mode = mode
        self._args = args  # noqa: the worker needs the full arg dict

    def run(self) -> None:
        if self._mode == "list":
            self.finished.emit(list_receipts(
                self._db_path,
                page=self._args.get("page", 1),
                page_size=self._args.get("page_size", 50),
                search_text=self._args.get("search_text", ""),
            ))
        elif self._mode == "detail":
            self.finished.emit(get_receipt(
                self._db_path, self._args["receipt_id"]))
        elif self._mode == "suppliers":
            result = _load_suppliers_for_combo(self._db_path)
            self.finished.emit(result)
        elif self._mode == "create":
            self.finished.emit(create_receipt_draft(
                self._db_path,
                supplier_id=self._args["supplier_id"],
                document_number=self._args["document_number"],
                document_type=self._args["document_type"],
                lines=self._args["lines"],
                notes=self._args.get("notes", ""),
                received_at=self._args.get("received_at", ""),
            ))
        elif self._mode == "approve":
            self.finished.emit(approve_receipt(
                self._db_path,
                receipt_id=self._args["receipt_id"],
                operator=self._args.get("operator", "Σύστημα"),
            ))
        elif self._mode == "cancel":
            self.finished.emit(cancel_receipt(
                self._db_path, self._args["receipt_id"]))


class GoodsReceiptPage(BasePage):
    """Φαρμακοποιός: δημιουργία, έλεγχος και έγκριση παραλαβών."""

    shutdown_ready = Signal()

    LIST_COLS = ["Προμηθευτής", "Αρ. Παραστατικού", "Τύπος",
                 "Ημ/νία Παραλαβής", "Κατάσταση"]
    LINE_COLS = ["#", "Barcode", "Προϊόν", "Ποσότητα",
                 "Κόστος Μον.", "Part No.", "Ημ. Λήξης"]

    def __init__(self, db_service, config, parent=None):
        self._db_path = (config.get("db_path", "encomm_erp.db")
                         if config else "encomm_erp.db")
        self._worker: _ReceiptWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False
        self._mode = ""                   # "list" | "detail" | "suppliers" | "create" | "approve" | "cancel"
        self._page = 1
        self._page_size = 50
        self._selected_receipt_id: str | None = None
        self._draft_lines: list[dict] = []  # in-memory lines for new draft
        self._supplier_map: dict[int, str] = {}  # id → name for combo
        self._pending_list_refresh = False   # deferred refresh after worker completes
        self._detail_active = False          # detail/editor pane visible?

        super().__init__(db_service, config, parent)

        # Deferred splitter re-proportioning (dies with the page)
        self._split_timer = QTimer(self)
        self._split_timer.setSingleShot(True)
        self._split_timer.timeout.connect(self._apply_detail_split)

    # ── UI construction ───────────────────────────────────────────────

    def build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ── LEFT panel: receipt list ──────────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 8, 0)
        left_lay.setSpacing(10)

        # Toolbar
        tb = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Αναζήτηση...")
        self._search.setMinimumHeight(36)
        self._search.returnPressed.connect(self._on_search)
        tb.addWidget(self._search, 2)

        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setToolTip("Ανανέωση λίστας")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self._do_list_refresh)
        tb.addWidget(self._refresh_btn)

        self._new_draft_btn = QPushButton("📝  Νέα Παραλαβή")
        self._new_draft_btn.setCursor(Qt.PointingHandCursor)
        self._new_draft_btn.setStyleSheet(self._btn_qss())
        self._new_draft_btn.clicked.connect(self._on_new_draft)
        tb.addWidget(self._new_draft_btn)

        left_lay.addLayout(tb)

        # Status label
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 13px;")
        left_lay.addWidget(self._status_lbl)

        # Receipt table
        self._list_table = QTableWidget(0, len(self.LIST_COLS))
        self._list_table.setHorizontalHeaderLabels(self.LIST_COLS)
        h = self._list_table.horizontalHeader()
        # Responsive columns: supplier stretches to absorb spare width,
        # data columns fit content — no fixed widths, no needless
        # horizontal scrollbar at normal desktop sizes.
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(self.LIST_COLS)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._list_table.verticalHeader().setVisible(False)
        self._list_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._list_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._list_table.itemSelectionChanged.connect(self._on_selection_changed)
        left_lay.addWidget(self._list_table, 1)

        # Pagination
        pg = QHBoxLayout()
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setToolTip("Προηγούμενη σελίδα")
        self._prev_btn.clicked.connect(lambda: self._go_page(-1))
        pg.addWidget(self._prev_btn)
        pg.addStretch()
        self._page_lbl = QLabel("Σελίδα 1")
        self._page_lbl.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 13px;")
        pg.addWidget(self._page_lbl)
        pg.addStretch()
        self._next_btn = QPushButton("▶")
        self._next_btn.setToolTip("Επόμενη σελίδα")
        self._next_btn.clicked.connect(lambda: self._go_page(1))
        pg.addWidget(self._next_btn)
        left_lay.addLayout(pg)

        # ── RIGHT panel: detail/editor stack (hidden in list mode) ───
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self._right_stack = QStackedWidget()

        # Index 0 — review panel (scrolls at reduced window sizes)
        self._review_panel = self._build_review_panel()
        self._right_stack.addWidget(self._wrap_scroll(self._review_panel))

        # Index 1 — editor panel (scrolls at reduced window sizes)
        self._editor_panel = self._build_editor_panel()
        self._right_stack.addWidget(self._wrap_scroll(self._editor_panel))

        right_lay.addWidget(self._right_stack, 1)

        # ── Add to splitter ───────────────────────────────────────────
        # No fixed pane widths: in list mode the right pane is hidden so
        # the receipt list uses the full page width; in detail mode the
        # splitter allocates ~42% list / ~58% detail (user-adjustable).
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self._splitter = splitter
        self._right_panel = right
        self._right_panel.hide()
        self.root_layout.addWidget(splitter, 1)

        # Kick off first load
        self._mode = "list"
        self._do_list_refresh()

    @staticmethod
    def _wrap_scroll(panel: QWidget) -> QScrollArea:
        """Wrap a detail panel so reduced window sizes scroll, never clip."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.viewport().setAutoFillBackground(False)
        area.setWidget(panel)
        return area

    # ── List/detail mode switching ────────────────────────────────────

    def _set_detail_visible(self, visible: bool) -> None:
        """Toggle the detail/editor pane.

        List mode (False): the pane is hidden and the receipt list takes
        the full page width.  Detail mode (True): the pane is revealed
        and the splitter allocates ~42% list / ~58% detail.
        """
        if self._detail_active == visible:
            return
        self._detail_active = visible
        self._right_panel.setVisible(visible)
        if visible:
            self._apply_detail_split()
            # Re-apply once the pane's show/layout pass has settled —
            # QSplitter redistributes on child-show and would otherwise
            # override the requested proportions.  Parented single-shot
            # timer: auto-cancelled if the page is destroyed first.
            self._split_timer.start(0)

    def _apply_detail_split(self) -> None:
        """Allocate ~42% list / ~58% detail of the current splitter width."""
        if not self._detail_active:
            return
        total = max(self._splitter.width(), 200)
        left_w = int(total * 0.42)
        self._splitter.setSizes([left_w, total - left_w])

    # ── Review panel ─────────────────────────────────────────────────

    def _build_review_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        # Receipt info
        self._review_info = QLabel("")
        self._review_info.setWordWrap(True)
        self._review_info.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 14px;")
        lay.addWidget(self._review_info)

        # Lines table
        self._review_lines = QTableWidget(0, len(self.LINE_COLS))
        self._review_lines.setHorizontalHeaderLabels(self.LINE_COLS)
        rh = self._review_lines.horizontalHeader()
        rh.setStretchLastSection(False)
        for i in range(len(self.LINE_COLS)):
            rh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(2, QHeaderView.Stretch)  # Προϊόν absorbs width
        self._review_lines.verticalHeader().setVisible(False)
        self._review_lines.setEditTriggers(QTableWidget.NoEditTriggers)
        self._review_lines.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self._review_lines, 1)

        # Controls — checkbox on its own row so narrow detail panes
        # never clip the action buttons below it.
        self._confirm_cb = QCheckBox(
            "✅  Επιβεβαιώνω ότι έχω ελέγξει όλες τις γραμμές και τα προϊόντα")
        self._confirm_cb.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 13px;")
        self._confirm_cb.toggled.connect(self._on_confirm_toggled)
        lay.addWidget(self._confirm_cb)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._approve_btn = QPushButton("✔  Έγκριση Παραλαβής")
        self._approve_btn.setCursor(Qt.PointingHandCursor)
        self._approve_btn.setStyleSheet(self._approve_btn_qss())
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self._on_approve)

        self._cancel_btn = QPushButton("✖  Ακύρωση")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setStyleSheet(self._cancel_btn_qss())
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)

        self._back_btn = QPushButton("←  Επιστροφή στη λίστα")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setStyleSheet(self._btn_qss())
        self._back_btn.clicked.connect(self._back_to_list)

        ctrl.addWidget(self._approve_btn)
        ctrl.addWidget(self._cancel_btn)
        ctrl.addStretch()
        ctrl.addWidget(self._back_btn)
        lay.addLayout(ctrl)

        return panel

    # ── Editor panel ─────────────────────────────────────────────────

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        # Form
        form = QFormLayout()

        self._edit_supplier = QComboBox()
        self._edit_supplier.setMinimumHeight(32)
        form.addRow("Προμηθευτής:", self._edit_supplier)

        self._edit_doc_number = QLineEdit()
        self._edit_doc_number.setPlaceholderText("π.χ. ΔΑ-2026-0042")
        self._edit_doc_number.setMinimumHeight(32)
        form.addRow("Αρ. Παραστατικού:", self._edit_doc_number)

        self._edit_doc_type = QComboBox()
        self._edit_doc_type.addItem("Δελτίο Αποστολής", "delivery_note")
        self._edit_doc_type.addItem("Τιμολόγιο Προμηθευτή", "supplier_invoice")
        self._edit_doc_type.setMinimumHeight(32)
        form.addRow("Τύπος:", self._edit_doc_type)

        self._edit_received_at = QLineEdit()
        self._edit_received_at.setText(date.today().isoformat())
        self._edit_received_at.setPlaceholderText("YYYY-MM-DD")
        self._edit_received_at.setMinimumHeight(32)
        form.addRow("Ημ/νία Παραλαβής:", self._edit_received_at)

        self._edit_notes = QLineEdit()
        self._edit_notes.setPlaceholderText("Σημειώσεις (προαιρετικά)")
        self._edit_notes.setMinimumHeight(32)
        form.addRow("Σημειώσεις:", self._edit_notes)

        lay.addLayout(form)

        # Line items section
        line_header = QHBoxLayout()
        line_lbl = QLabel("Γραμμές Παραλαβής")
        line_lbl.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 14px; font-weight: bold;")
        line_header.addWidget(line_lbl, 1)
        self._add_line_btn = QPushButton("➕  Προσθήκη Γραμμής")
        self._add_line_btn.setCursor(Qt.PointingHandCursor)
        self._add_line_btn.setStyleSheet(self._btn_qss())
        self._add_line_btn.clicked.connect(self._on_add_line)
        line_header.addWidget(self._add_line_btn)
        self._remove_line_btn = QPushButton("➖  Αφαίρεση")
        self._remove_line_btn.setCursor(Qt.PointingHandCursor)
        self._remove_line_btn.setStyleSheet(self._btn_qss())
        self._remove_line_btn.clicked.connect(self._on_remove_line)
        line_header.addWidget(self._remove_line_btn)
        lay.addLayout(line_header)

        self._edit_lines_table = QTableWidget(0, len(self.LINE_COLS))
        self._edit_lines_table.setHorizontalHeaderLabels(self.LINE_COLS)
        eh = self._edit_lines_table.horizontalHeader()
        eh.setStretchLastSection(False)
        for i in range(len(self.LINE_COLS)):
            eh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        eh.setSectionResizeMode(2, QHeaderView.Stretch)  # Προϊόν absorbs width
        self._edit_lines_table.verticalHeader().setVisible(False)
        self._edit_lines_table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self._edit_lines_table, 1)

        # Save / cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_draft_btn = QPushButton("💾  Αποθήκευση Πρόχειρης Παραλαβής")
        self._save_draft_btn.setCursor(Qt.PointingHandCursor)
        self._save_draft_btn.setStyleSheet(self._approve_btn_qss())
        self._save_draft_btn.clicked.connect(self._on_save_draft)
        btn_row.addWidget(self._save_draft_btn)

        self._cancel_edit_btn = QPushButton("✖  Ακύρωση Επεξεργασίας")
        self._cancel_edit_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_edit_btn.setStyleSheet(self._cancel_btn_qss())
        self._cancel_edit_btn.clicked.connect(self._back_to_list)
        btn_row.addWidget(self._cancel_edit_btn)

        lay.addLayout(btn_row)
        return panel

    # ── Button QSS ────────────────────────────────────────────────────

    @staticmethod
    def _btn_qss() -> str:
        return (
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 14px; font-size: 13px; "
            f"font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    @staticmethod
    def _approve_btn_qss() -> str:
        return (
            "QPushButton { background: #10B981; color: white; "
            "border-radius: 6px; padding: 8px 14px; font-size: 13px; "
            "font-weight: bold; border: none; }"
            "QPushButton:hover { background: #059669; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    @staticmethod
    def _cancel_btn_qss() -> str:
        return (
            "QPushButton { background: #EF4444; color: white; "
            "border-radius: 6px; padding: 8px 14px; font-size: 13px; "
            "font-weight: bold; border: none; }"
            "QPushButton:hover { background: #DC2626; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    # ── List operations ───────────────────────────────────────────────

    def refresh(self) -> None:
        self._page = 1
        self._do_list_refresh()

    def _on_search(self) -> None:
        self.refresh()

    def _go_page(self, delta: int) -> None:
        self._page += delta
        self._do_list_refresh()

    def _do_list_refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._mode = "list"
        self._refresh_btn.setEnabled(False)
        self._set_status("🔄 Φόρτωση παραλαβών...", styles.TEXT_MUTED)
        self._launch_worker("list", {"page": self._page, "page_size": self._page_size,
                                       "search_text": self._search.text().strip()})

    def _on_selection_changed(self) -> None:
        rows = {it.row() for it in self._list_table.selectedItems()}
        if not rows or len(rows) != 1:
            return
        r = list(rows)[0]
        rid = self._list_table.item(r, 0).data(Qt.UserRole)
        if rid:
            self._load_detail(rid)

    def _load_detail(self, receipt_id: str) -> None:
        if self._loading:
            return
        self._selected_receipt_id = receipt_id
        self._loading = True
        self._mode = "detail"
        self._set_status("🔄 Φόρτωση λεπτομερειών...", styles.TEXT_MUTED)
        self._launch_worker("detail", {"receipt_id": receipt_id})

    # ── Draft creation ────────────────────────────────────────────────

    def _on_new_draft(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._mode = "suppliers"
        self._set_status("🔄 Φόρτωση προμηθευτών...", styles.TEXT_MUTED)
        self._launch_worker("suppliers", {})

    def _start_editor(self, suppliers: list[tuple[int, str]]) -> None:
        """Populate editor and switch to it."""
        self._edit_supplier.clear()
        self._supplier_map.clear()
        for sid, name in suppliers:
            self._edit_supplier.addItem(name, sid)
            self._supplier_map[sid] = name

        self._edit_doc_number.clear()
        self._edit_doc_type.setCurrentIndex(0)
        self._edit_received_at.setText(date.today().isoformat())
        self._edit_notes.clear()
        self._draft_lines = []
        self._rebuild_edit_lines_table()
        self._right_stack.setCurrentIndex(1)  # editor
        self._set_detail_visible(True)

    def _on_add_line(self) -> None:
        self._draft_lines.append({
            "barcode": "",
            "product_name": "",
            "received_qty": 0,
            "unit_cost": 0.0,
            "batch_number": "",
            "expiry_date": "",
        })
        self._rebuild_edit_lines_table()

    def _on_remove_line(self) -> None:
        rows = {it.row() for it in self._edit_lines_table.selectedItems()}
        if not rows:
            QMessageBox.information(self, "Πληροφορία",
                                    "Επιλέξτε μια γραμμή για αφαίρεση.")
            return
        idx = list(rows)[0]
        if 0 <= idx < len(self._draft_lines):
            del self._draft_lines[idx]
        self._rebuild_edit_lines_table()

    def _rebuild_edit_lines_table(self) -> None:
        t = self._edit_lines_table
        t.setRowCount(len(self._draft_lines))
        for i, li in enumerate(self._draft_lines):
            t.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            t.setItem(i, 1, QTableWidgetItem(li.get("barcode", "")))
            t.setItem(i, 2, QTableWidgetItem(li.get("product_name", "")))
            t.setItem(i, 3, QTableWidgetItem(str(li.get("received_qty", 0))))
            t.setItem(i, 4, QTableWidgetItem(str(li.get("unit_cost", 0.0))))
            t.setItem(i, 5, QTableWidgetItem(li.get("batch_number", "")))
            t.setItem(i, 6, QTableWidgetItem(li.get("expiry_date", "")))

    def _on_save_draft(self) -> None:
        """Read editor form fields and call create_receipt_draft."""
        if self._loading:
            return

        # Read supplier
        idx = self._edit_supplier.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, "Σφάλμα", "Επιλέξτε προμηθευτή.")
            return
        supplier_id = self._edit_supplier.currentData()

        doc_number = self._edit_doc_number.text().strip()
        if not doc_number:
            QMessageBox.warning(self, "Σφάλμα",
                                "Ο αριθμός παραστατικού είναι υποχρεωτικός.")
            return

        doc_type = self._edit_doc_type.currentData()
        received_at = self._edit_received_at.text().strip()

        # Read lines from table (editable cells)
        lines: list[dict] = []
        for i in range(self._edit_lines_table.rowCount()):
            barcode = self._edit_lines_table.item(i, 1)
            name = self._edit_lines_table.item(i, 2)
            qty = self._edit_lines_table.item(i, 3)
            cost = self._edit_lines_table.item(i, 4)
            batch = self._edit_lines_table.item(i, 5)
            expiry = self._edit_lines_table.item(i, 6)

            barcode_str = barcode.text().strip() if barcode else ""
            name_str = name.text().strip() if name else ""

            try:
                qty_val = int(qty.text().strip()) if qty else 0
            except ValueError:
                qty_val = 0
            try:
                cost_val = float(cost.text().strip()) if cost else 0.0
            except ValueError:
                cost_val = 0.0

            batch_str = batch.text().strip() if batch else ""
            expiry_str = expiry.text().strip() if expiry else ""

            lines.append({
                "barcode": barcode_str,
                "product_name": name_str,
                "received_qty": qty_val,
                "unit_cost": cost_val,
                "batch_number": batch_str,
                "expiry_date": expiry_str,
            })

        self._loading = True
        self._mode = "create"
        self._set_status("🔄 Αποθήκευση πρόχειρης παραλαβής...", styles.TEXT_MUTED)
        self._launch_worker("create", {
            "supplier_id": supplier_id,
            "document_number": doc_number,
            "document_type": doc_type,
            "lines": lines,
            "notes": self._edit_notes.text().strip(),
            "received_at": received_at,
        })

    # ── Approval ─────────────────────────────────────────────────────

    def _on_confirm_toggled(self, checked: bool) -> None:
        self._approve_btn.setEnabled(checked and not self._loading)

    def _on_approve(self) -> None:
        if self._loading or not self._selected_receipt_id:
            return

        reply = QMessageBox.question(
            self, "Επιβεβαίωση Έγκρισης",
            "Είστε βέβαιοι ότι θέλετε να εγκρίνετε αυτή την παραλαβή;\n\n"
            "Μετά την έγκριση, το απόθεμα θα ενημερωθεί και η ενέργεια "
            "δεν μπορεί να αναιρεθεί από αυτή τη σελίδα.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self._loading = True
        self._mode = "approve"
        self._set_status("🔄 Έγκριση παραλαβής...", styles.TEXT_MUTED)
        self._launch_worker("approve", {
            "receipt_id": self._selected_receipt_id,
            "operator": "Φαρμακοποιός",
        })

    # ── Cancel ───────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        if self._loading or not self._selected_receipt_id:
            return

        reply = QMessageBox.question(
            self, "Επιβεβαίωση Ακύρωσης",
            "Είστε βέβαιοι ότι θέλετε να ακυρώσετε αυτή την παραλαβή;",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self._loading = True
        self._mode = "cancel"
        self._set_status("🔄 Ακύρωση παραλαβής...", styles.TEXT_MUTED)
        self._launch_worker("cancel", {
            "receipt_id": self._selected_receipt_id,
        })

    def _back_to_list(self) -> None:
        self._set_detail_visible(False)  # full-width list again
        self._selected_receipt_id = None
        self._confirm_cb.setChecked(False)
        self._draft_lines = []
        # Drop row selection so re-selecting the same receipt re-opens it
        self._list_table.clearSelection()

    def _request_list_refresh(self) -> None:
        """Schedule a list refresh.  If a worker is active, defer until it finishes.
        Otherwise refresh immediately.  Only used by success handlers."""
        if self._loading:
            self._pending_list_refresh = True
        else:
            self._do_list_refresh()

    # ── Worker lifecycle ─────────────────────────────────────────────

    def _launch_worker(self, mode: str, args: dict) -> None:
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _ReceiptWorker(self._db_path, mode, args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_result)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_worker_done)
        self._thread.start()

    def _on_worker_result(self, result: Any) -> None:
        if self._close_pending:
            return

        mode = self._mode

        if mode == "list":
            self._on_list_result(result)
        elif mode == "detail":
            self._on_detail_result(result)
        elif mode == "suppliers":
            self._on_suppliers_result(result)
        elif mode == "create":
            self._on_create_result(result)
        elif mode == "approve":
            self._on_approve_result(result)
        elif mode == "cancel":
            self._on_cancel_result(result)

    # ── Result handlers ──────────────────────────────────────────────

    def _on_list_result(self, result: ReceiptListResult) -> None:
        if not result.ok:
            self._set_status(result.error_message, styles.RED)
            self._list_table.setRowCount(0)
            return

        items = result.items
        self._list_table.setRowCount(len(items))
        for r_idx, (rid, sup_name, doc_num, doc_type, recv_at, status) in enumerate(items):
            sn = QTableWidgetItem(sup_name)
            sn.setData(Qt.UserRole, rid)
            self._list_table.setItem(r_idx, 0, sn)
            self._list_table.setItem(r_idx, 1, QTableWidgetItem(doc_num))
            dt_label = DOC_TYPE_LABELS.get(doc_type, doc_type)
            self._list_table.setItem(r_idx, 2, QTableWidgetItem(dt_label))
            self._list_table.setItem(r_idx, 3, QTableWidgetItem(recv_at))
            st_item = QTableWidgetItem(STATUS_LABELS.get(status, status))
            hex_color = STATUS_COLORS.get(status, styles.TEXT_PRIMARY)
            st_item.setForeground(QColor(hex_color))
            self._list_table.setItem(r_idx, 4, st_item)

        self._page = result.page
        tp = max(1, (result.total + result.page_size - 1) // result.page_size)
        self._page_lbl.setText(f"Σελίδα {result.page} από {tp}")
        self._prev_btn.setEnabled(result.page > 1)
        self._next_btn.setEnabled(result.page < tp)
        self._set_status(f"Σύνολο: {result.total} παραλαβές", styles.TEXT_MUTED)

    def _on_detail_result(self, result: GetReceiptResult) -> None:
        if not result.ok:
            self._set_status(result.error_message, styles.RED)
            self._set_detail_visible(False)
            return

        rec = result.receipt
        st_label = STATUS_LABELS.get(rec.status, rec.status)
        info = (
            f"<b>Προμηθευτής:</b> {rec.supplier_name} &nbsp;&nbsp;|&nbsp;&nbsp;"
            f"<b>Αρ. Παραστατικού:</b> {rec.document_number} &nbsp;&nbsp;|&nbsp;&nbsp;"
            f"<b>Τύπος:</b> {DOC_TYPE_LABELS.get(rec.document_type, rec.document_type)}<br>"
            f"<b>Ημ/νία Παραλαβής:</b> {rec.received_at} &nbsp;&nbsp;|&nbsp;&nbsp;"
            f"<b>Κατάσταση:</b> <span style='color:{STATUS_COLORS.get(rec.status, '#fff')}'>"
            f"{st_label}</span>"
        )
        if rec.approved_at:
            info += (f" &nbsp;&nbsp;|&nbsp;&nbsp;"
                     f"<b>Εγκρίθηκε:</b> {rec.approved_at} από {rec.approved_by or '—'}")
        self._review_info.setText(info)

        lines = rec.lines
        self._review_lines.setRowCount(len(lines))
        for i, li in enumerate(lines):
            self._review_lines.setItem(i, 0, QTableWidgetItem(str(li.line_number)))
            self._review_lines.setItem(i, 1, QTableWidgetItem(li.barcode))
            self._review_lines.setItem(i, 2, QTableWidgetItem(li.product_name))
            self._review_lines.setItem(i, 3, QTableWidgetItem(str(li.received_qty)))
            self._review_lines.setItem(i, 4, QTableWidgetItem(f"{li.unit_cost:.2f}"))
            self._review_lines.setItem(i, 5, QTableWidgetItem(li.batch_number))
            self._review_lines.setItem(i, 6, QTableWidgetItem(li.expiry_date))

        # Gate controls based on status
        is_draft = (rec.status == "draft")
        self._confirm_cb.setEnabled(is_draft)
        self._confirm_cb.setChecked(False)
        self._approve_btn.setEnabled(False)  # requires confirm checkbox
        self._cancel_btn.setEnabled(is_draft)

        self._right_stack.setCurrentIndex(0)  # review panel
        self._set_detail_visible(True)
        self._set_status(f"Παραλαβή {rec.id} — {st_label}", styles.TEXT_MUTED)

    def _on_suppliers_result(self, result: list) -> None:
        self._start_editor(result)
        self._set_status("Συμπληρώστε τα στοιχεία και αποθηκεύστε.", styles.TEXT_MUTED)

    def _on_create_result(self, result: CreateDraftResult) -> None:
        if not result.ok:
            self._set_status(result.error_message, styles.RED)
            QMessageBox.warning(self, "Σφάλμα", result.error_message)
            return

        QMessageBox.information(self, "Επιτυχία",
                                f"Η πρόχειρη παραλαβή δημιουργήθηκε.\nID: {result.receipt_id}")
        self._back_to_list()
        self._request_list_refresh()

    def _on_approve_result(self, result: ApproveReceiptResult) -> None:
        if not result.ok:
            self._set_status(result.error_message, styles.RED)
            QMessageBox.warning(self, "Σφάλμα", f"Αποτυχία έγκρισης:\n{result.error_message}")
            self._loading = False  # re-enable early — error path
            self._refresh_btn.setEnabled(True)
            return

        QMessageBox.information(
            self, "Επιτυχία",
            f"Η παραλαβή εγκρίθηκε!\n"
            f"{result.lines_applied} γραμμές, {result.total_units} μονάδες προστέθηκαν στο απόθεμα.")

        # Refresh related views
        self._refresh_related_pages()
        self._back_to_list()
        self._request_list_refresh()

    def _on_cancel_result(self, result: CancelReceiptResult) -> None:
        if not result.ok:
            self._set_status(result.error_message, styles.RED)
            QMessageBox.warning(self, "Σφάλμα", result.error_message)
            return
        QMessageBox.information(self, "Ακύρωση", "Η παραλαβή ακυρώθηκε.")
        self._back_to_list()
        self._request_list_refresh()

    # ── Cross-page refresh ───────────────────────────────────────────

    def _refresh_related_pages(self) -> None:
        """After approval, refresh inventory and stock-movements views."""
        mw = self.window()
        if mw is None:
            return
        try:
            pages = getattr(mw, "_pages", {})
            for key in ("inventory", "stock_movements"):
                page = pages.get(key)
                if page is not None and hasattr(page, "refresh"):
                    page.refresh()
        except Exception:
            pass  # best-effort, never crash

    # ── Shutdown ─────────────────────────────────────────────────────

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
            self._worker.finished.disconnect(self._on_worker_result)
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

    def _on_worker_done(self) -> None:
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()
            return
        # Deferred list refresh — was requested while worker was still alive
        if self._pending_list_refresh:
            self._pending_list_refresh = False
            self._do_list_refresh()

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 13px;")
