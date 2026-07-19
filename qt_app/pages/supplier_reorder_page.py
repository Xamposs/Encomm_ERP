"""Supplier Reorder Candidates — read-only UI + in-memory draft (P3.2/P3.3).

Displays low-stock products grouped by supplier, plus an unassigned
section for products without a valid supplier.  Data comes exclusively
from :func:`qt_app.data_source.load_supplier_reorder_candidates` — no
SQL, no writes, no persistence.

On top of the read-only view (P3.2), P3.3 adds a strictly in-memory
"Πρόχειρο Αναπαραγγελίας" (reorder draft).  The draft lives only inside
this page instance: it is never written to SQLite, files, logs, the
clipboard, or any external service.  Adding a candidate to the draft
does NOT remove it from the eligible-candidate view — the candidate
stays visible and the draft is rendered in a clearly separate area.

Quantities are entered manually by the user.  This module never
infers, recommends, calculates, or prefills a reorder quantity.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QVBoxLayout, QWidget, QScrollArea,
    QFrame, QAbstractItemView,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_supplier_reorder_candidates,
    SupplierReorderResult, SupplierReorderGroup,
    UnassignedReorderProduct, ReorderCandidate,
)


# ── In-memory draft model ───────────────────────────────────────────────


@dataclass
class _DraftLine:
    """One manually-built line of the in-memory reorder draft.

    ``quantity`` is a positive integer entered by the user.  The product
    snapshot (supplier / barcode / name / stock / threshold / expiry /
    price) is captured from the candidate at the moment the line is
    first added so the draft remains stable across refreshes; it is NOT
    re-read from SQLite after the line is added.
    """

    supplier_id: int
    supplier_name: str
    barcode: str
    name: str
    stock: int
    threshold: int
    expiry_date: str
    price: float
    quantity: int


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
    """Read-only view of supplier reorder candidates + in-memory draft."""

    shutdown_ready = Signal()

    # Columns for grouped (assigned) candidates — col 6 is an inline
    # quantity QSpinBox, col 7 is the 'add to draft' action button.
    _GROUPED_COLS = [
        "Barcode", "Προϊόν", "Απόθεμα", "Όριο", "Λήξη", "Τιμή",
        "Ποσότητα",
        "Προσθήκη",
    ]
    # Columns for unassigned products — one extra for the reason
    _UNASSIGNED_COLS = [
        "Barcode", "Προϊόν", "Απόθεμα", "Όριο", "Λήξη", "Τιμή", "Αιτία",
    ]
    # Columns for one supplier's draft section — last col is a per-line
    # remove action.  Quantity is a user-edited QSpinBox in column 6.
    _DRAFT_LINE_COLS = [
        "Barcode", "Προϊόν", "Απόθεμα", "Όριο", "Λήξη", "Τιμή",
        "Ποσότητα", "Αφαίρεση",
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
        # In-memory draft — keyed by barcode (one line per product, ever).
        # Snapshot of the most recent successful result so that an empty
        # refresh does not silently drop a non-empty draft.
        self._draft: dict[str, _DraftLine] = {}
        self._last_result: SupplierReorderResult | None = None
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
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
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

        # Draft area lives at the TOP of the scroll content, candidates below.
        self._draft_frame = QFrame()
        self._draft_frame.setObjectName("draftFrame")
        self._draft_frame.setStyleSheet(
            f"QFrame#draftFrame {{ background: {styles.SURFACE_ALT}; "
            f"border: 1px solid {styles.ACCENT}; border-radius: 8px; }}")
        self._draft_layout = QVBoxLayout(self._draft_frame)
        self._draft_layout.setContentsMargins(12, 12, 12, 12)
        self._draft_layout.setSpacing(8)
        self._draft_header = QLabel("Πρόχειρο Αναπαραγγελίας")
        self._draft_header.setStyleSheet(
            f"color: {styles.ACCENT}; font-size: 14px; font-weight: bold;")
        self._draft_layout.addWidget(self._draft_header)
        self._draft_summary = QLabel("")
        self._draft_summary.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px;")
        self._draft_layout.addWidget(self._draft_summary)

        # Per-supplier draft sections.  Each supplier with at least one
        # draft line gets its own QGroupBox (titled 'Προμηθευτής: <name>')
        # containing a draft-line table.  This makes the supplier that
        # owns every line unambiguously visible — a flat mixed table
        # would not.  The container is rebuilt on every render.
        self._draft_sections_host = QWidget()
        self._draft_sections_layout = QVBoxLayout(self._draft_sections_host)
        self._draft_sections_layout.setContentsMargins(0, 0, 0, 0)
        self._draft_sections_layout.setSpacing(10)
        self._draft_layout.addWidget(self._draft_sections_host)

        self._draft_empty = QLabel(
            "Το πρόχειρο αναπαραγγελίας είναι άδειο. "
            "Επιλέξτε προϊόντα από τους υποψηφίους παρακάτω.")
        self._draft_empty.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 13px; padding: 8px;")
        self._draft_empty.setWordWrap(True)
        self._draft_layout.addWidget(self._draft_empty)

        self._discard_btn = QPushButton("🗑  Καθαρισμός προσχείου")
        self._discard_btn.setStyleSheet(self._danger_btn_qss())
        self._discard_btn.setCursor(Qt.PointingHandCursor)
        self._discard_btn.clicked.connect(self._on_discard_clicked)
        self._discard_btn.setVisible(False)
        self._draft_layout.addWidget(self._discard_btn)

        self._scroll_layout.addWidget(self._draft_frame)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_widget)
        self.root_layout.addWidget(self._scroll, 1)

        # Render the (empty) initial draft state so the discard button
        # is explicitly hidden, the empty label is explicitly shown,
        # and the draft table is explicitly hidden.  This avoids
        # leaving those widgets in the default "never-shown" state.
        self._render_draft()

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

    @staticmethod
    def _danger_btn_qss() -> str:
        return (
            f"QPushButton {{ background: {styles.RED}; color: white; "
            f"border-radius: 6px; padding: 6px 14px; font-size: 12px; "
            f"font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #c53030; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }"
        )

    # ── Refresh / worker lifecycle ────────────────────────────────────

    def _on_refresh_clicked(self) -> None:
        """Refresh button handler — enforces draft protection.

        A non-empty draft is never silently discarded on refresh: the
        user must explicitly clear it first.  We surface a clear Greek
        message instead of touching the data.
        """
        if self._draft:
            self._state_lbl.show()
            self._state_lbl.setText(
                "Υπάρχει μη άδειο πρόχειρο αναπαραγγελίας. "
                "Καθαρίστε το πρόχειρο (Καθαρισμός προσχείου) πριν την "
                "ανανέωση για να μην αλλοιωθεί.")
            self._state_lbl.setStyleSheet(
                f"color: {styles.AMBER}; font-size: 13px; padding: 12px;")
            return
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._threshold = self._threshold_spin.value()
        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._set_state("🔄 Φόρτωση υποψηφίων αναπαραγγελίας...",
                        styles.TEXT_MUTED)
        self._summary.setText("")
        self._clear_candidates()

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
            self._clear_candidates()
            return

        self._last_result = result
        groups = result.groups
        unassigned = result.unassigned

        if not groups and not unassigned:
            self._set_state(
                "Δεν βρέθηκαν προϊόντα κάτω από το όριο αναπαραγγελίας.",
                styles.TEXT_MUTED)
            self._summary.setText("")
            self._clear_candidates()
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
                group_id=group.supplier_id,
                supplier_name=group.supplier_name,
                add_action=True)
            gb_layout.addWidget(table)
            self._scroll_layout.insertWidget(
                self._scroll_layout.count() - 1, gb)

        # ── Build unassigned section (never draft-eligible) ──────────
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
        group_id: int | None = None,
        supplier_name: str | None = None,
        add_action: bool = False,
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
            if add_action and group_id is not None and supplier_name:
                # Inline quantity spinbox — starts at 0 (blank-equivalent).
                qty_spin = QSpinBox()
                qty_spin.setRange(0, 100000)
                qty_spin.setValue(0)
                qty_spin.setMinimumHeight(30)
                qty_spin.setProperty("candidate_barcode", p.barcode)
                table.setCellWidget(r, len(cols) - 2, qty_spin)

                add_btn = QPushButton("➕  Προσθήκη")
                add_btn.setStyleSheet(self._btn_qss())
                add_btn.setCursor(Qt.PointingHandCursor)
                # Disabled at quantity 0 (the 'no selection' state).
                add_btn.setEnabled(False)
                add_btn.setProperty("candidate_barcode", p.barcode)

                if p.barcode in self._draft:
                    add_btn.setEnabled(False)
                    add_btn.setText("✓  Στο πρόχειρο")
                    qty_spin.setValue(self._draft[p.barcode].quantity)
                    qty_spin.setEnabled(False)

                # Spinbox change toggles the add button.
                qty_spin.valueChanged.connect(
                    lambda v, _btn=add_btn: _btn.setEnabled(v > 0))
                add_btn.clicked.connect(
                    lambda _checked=False, _btn=add_btn, _spin=qty_spin,
                    _g=group_id, _s=supplier_name, _p=p:
                    self._add_candidate_via_button(
                        _btn, _spin, _g, _s, _p))
                table.setCellWidget(r, len(cols) - 1, add_btn)
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
        """Slot connected to ``thread.finished``.

        Drops the page's ``_loading`` flag and re-enables the refresh
        button immediately.  The references to ``_worker`` and
        ``_thread`` are cleared on the NEXT event-loop iteration via a
        zero-delay timer: this slot runs *inside* the dispatch of
        ``thread.finished``, and dropping the Python references
        in-flight can free the underlying C++ objects before their
        connected ``deleteLater`` slots have run.  Deferring one tick
        lets Qt finish dispatching the signal first.
        """
        self._loading = False
        self._refresh_btn.setEnabled(True)
        close_pending = self._close_pending
        self._close_pending = False

        def _drop_refs() -> None:
            self._worker = None
            self._thread = None
            if close_pending:
                self.shutdown_ready.emit()
        QTimer.singleShot(0, _drop_refs)

    # ── Draft workflow (in-memory only) ───────────────────────────────

    def _add_candidate_via_button(
        self,
        button: QPushButton,
        spinbox: QSpinBox,
        supplier_id: int,
        supplier_name: str,
        candidate: ReorderCandidate,
    ) -> None:
        """User-facing 'add to draft' handler bound to each candidate row.

        Reads the exact quantity from the inline spinbox and passes it
        to ``_add_to_draft``.  The spinbox starts at 0 (blank-equivalent)
        and the button is disabled while the quantity is 0, so this
        handler should only receive a positive integer.  Repeated clicks
        on a candidate already in the draft are a no-op (the button is
        disabled after a successful add and re-enabled only when the
        line is removed or the draft is cleared).
        """
        if candidate.barcode in self._draft:
            return
        quantity = spinbox.value()
        if quantity <= 0:
            return
        if self._add_to_draft(supplier_id, supplier_name, candidate, quantity):
            button.setEnabled(False)
            button.setText("✓  Στο πρόχειρο")
            spinbox.setEnabled(False)

    def _add_to_draft(
        self,
        supplier_id: int,
        supplier_name: str,
        candidate: ReorderCandidate,
        quantity: int,
    ) -> bool:
        """Add a candidate to the draft with an explicit quantity.

        Returns True on success, False on invalid input.  Never writes
        outside this instance.  ``quantity`` MUST be a positive integer
        — there is NO default; callers must obtain one from the user's
        inline QSpinBox or pass an explicit value.

        If the product is already in the draft, this is a no-op and
        returns False: a product never appears twice, and the quantity
        of an existing line changes ONLY through the explicit quantity
        editor in the draft section (see :meth:`update_quantity`) or
        another equally explicit user action.
        """
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            return False
        if quantity <= 0:
            return False
        if candidate.barcode in self._draft:
            # Repeated add attempts never silently increase quantity.
            return False
        self._draft[candidate.barcode] = _DraftLine(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            barcode=candidate.barcode,
            name=candidate.name,
            stock=candidate.stock,
            threshold=candidate.threshold,
            expiry_date=candidate.expiry_date,
            price=candidate.price,
            quantity=quantity,
        )
        self._render_draft()
        self._refresh_add_button_states()
        return True

    def update_quantity(self, barcode: str, quantity: int) -> bool:
        """Set the quantity on an existing draft line.

        This is the explicit user-facing entry point for changing a
        quantity — wired to the QSpinBox in each draft line.  Updating
        never creates a new line.  Returns True on success, False if
        the barcode is not in the draft or the quantity is invalid.
        """
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            return False
        if quantity <= 0:
            return False
        line = self._draft.get(barcode)
        if line is None:
            return False
        line.quantity = quantity
        self._render_draft()
        return True

    def remove_line(self, barcode: str) -> bool:
        """Remove a line from the draft.  Returns True if a line was
        removed, False otherwise.  Removing restores the candidate to
        its normal eligible state — the candidate's add button is
        re-enabled on the next render.
        """
        if barcode in self._draft:
            del self._draft[barcode]
            self._render_draft()
            self._refresh_add_button_states()
            return True
        return False

    def clear_draft(self) -> None:
        """Discard the entire local draft.  Does not touch anything else."""
        self._draft.clear()
        self._render_draft()
        self._refresh_add_button_states()

    def draft_lines(self) -> tuple[_DraftLine, ...]:
        """Return a deterministic snapshot of all draft lines.

        Order: supplier name asc → supplier id asc → product name asc
        → barcode asc.  Grouping by supplier is done in the renderer;
        this flattened view is still deterministically ordered so the
        per-supplier sections are stable.
        """
        return tuple(sorted(
            self._draft.values(),
            key=lambda l: (l.supplier_name, l.supplier_id, l.name, l.barcode),
        ))

    def draft_groups(self) -> tuple[tuple[int, str, tuple[_DraftLine, ...]], ...]:
        """Return the draft bucketed by supplier, deterministically.

        Each entry is ``(supplier_id, supplier_name, lines)`` where
        ``lines`` is sorted by product name then barcode.  Suppliers
        are sorted by name then id.  Used by the renderer to build
        visible per-supplier sections.
        """
        buckets: dict[tuple[int, str], list[_DraftLine]] = {}
        for line in self._draft.values():
            buckets.setdefault((line.supplier_id, line.supplier_name), []) \
                .append(line)
        ordered_keys = sorted(buckets.keys(), key=lambda k: (k[1], k[0]))
        out: list[tuple[int, str, tuple[_DraftLine, ...]]] = []
        for key in ordered_keys:
            lines = sorted(
                buckets[key], key=lambda l: (l.name, l.barcode))
            out.append((key[0], key[1], tuple(lines)))
        return tuple(out)

    def is_draft_empty(self) -> bool:
        return not self._draft

    # ── Draft rendering ───────────────────────────────────────────────

    def _render_draft(self) -> None:
        """Rebuild the per-supplier draft sections from the model.

        Each supplier with at least one line gets its own titled
        QGroupBox so the owning supplier is unambiguously visible.
        Within a section, lines are sorted by product name then
        barcode.  Empty state, summary line, and discard button
        visibility are kept consistent.
        """
        groups = self.draft_groups()
        line_count = sum(len(lines) for _, _, lines in groups)
        supplier_count = len(groups)

        # ── Tear down any previous section widgets ───────────────────
        # We remove them from the layout and detach from their parent
        # immediately so a subsequent ``findChildren`` scan does not
        # see stale sections whose ``deleteLater`` hasn't fired yet.
        while self._draft_sections_layout.count():
            item = self._draft_sections_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if line_count == 0:
            self._draft_summary.setText("")
            self._draft_summary.hide()
            self._draft_empty.show()
            self._draft_sections_host.hide()
            self._discard_btn.setVisible(False)
            return

        self._draft_summary.setText(
            f"Γραμμές: {line_count}  |  Προμηθευτές: {supplier_count}")
        self._draft_summary.show()
        self._draft_empty.hide()
        self._draft_sections_host.show()
        self._discard_btn.setVisible(True)

        # ── Build one QGroupBox per supplier ─────────────────────────
        for supplier_id, supplier_name, lines in groups:
            gb = QGroupBox(f"Προμηθευτής: {supplier_name}")
            gb.setStyleSheet(self._group_box_qss())
            gb_layout = QVBoxLayout(gb)
            gb_layout.setContentsMargins(8, 20, 8, 8)
            gb_layout.addWidget(self._build_draft_section_table(lines))
            self._draft_sections_layout.addWidget(gb)

    def _build_draft_section_table(
        self, lines: tuple[_DraftLine, ...],
    ) -> QTableWidget:
        """Build the draft-line table for one supplier section."""
        table = QTableWidget(0, len(self._DRAFT_LINE_COLS))
        table.setHorizontalHeaderLabels(self._DRAFT_LINE_COLS)
        h = table.horizontalHeader()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, len(self._DRAFT_LINE_COLS)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setRowCount(len(lines))

        for r, line in enumerate(lines):
            table.setItem(r, 0, QTableWidgetItem(line.barcode))
            table.setItem(r, 1, QTableWidgetItem(line.name))
            table.setItem(r, 2, QTableWidgetItem(str(line.stock)))
            table.setItem(r, 3, QTableWidgetItem(str(line.threshold)))
            table.setItem(r, 4, QTableWidgetItem(line.expiry_date))
            table.setItem(r, 5, QTableWidgetItem(f"€{line.price:.2f}"))

            # Explicit quantity editor — the only way to change qty.
            qty_spin = QSpinBox()
            qty_spin.setRange(1, 100000)
            qty_spin.setValue(line.quantity)
            qty_spin.setMinimumHeight(30)
            qty_spin.valueChanged.connect(
                lambda v, b=line.barcode: self.update_quantity(b, int(v)))
            table.setCellWidget(r, 6, qty_spin)

            rm_btn = QPushButton("✕  Αφαίρεση")
            rm_btn.setStyleSheet(self._danger_btn_qss())
            rm_btn.setCursor(Qt.PointingHandCursor)
            rm_btn.clicked.connect(
                lambda _checked=False, b=line.barcode: self.remove_line(b))
            table.setCellWidget(r, 7, rm_btn)
        return table

    def _refresh_add_button_states(self) -> None:
        """Re-enable / re-disable candidate 'add' controls to match
        the draft state.

        Walks the candidate group boxes installed by the last
        successful refresh.  Both the add button and the inline
        quantity spinbox are tagged with a ``candidate_barcode``
        dynamic property by :meth:`_build_product_table`.

        Buttons and spinboxes for the same barcode are processed
        together in a single pass so that (a) spinbox value changes
        are signal-blocked and (b) the button's enabled state is
        derived from the actual spinbox value rather than the draft
        status alone — a non-draft candidate with quantity 0 must
        have a disabled button.
        """
        barcodes_in_draft = set(self._draft.keys())

        # Build barcode-indexed maps so we can process controls for
        # the same candidate together.
        btn_map: dict[str, QPushButton] = {}
        for btn in self._scroll_widget.findChildren(QPushButton):
            bc = btn.property("candidate_barcode")
            if bc is not None:
                btn_map[bc] = btn

        spin_map: dict[str, QSpinBox] = {}
        for spin in self._scroll_widget.findChildren(QSpinBox):
            bc = spin.property("candidate_barcode")
            if bc is not None:
                spin_map[bc] = spin

        all_barcodes = set(btn_map) | set(spin_map)
        for barcode in all_barcodes:
            in_draft = barcode in barcodes_in_draft
            spin = spin_map.get(barcode)
            btn = btn_map.get(barcode)

            if spin is not None:
                spin.blockSignals(True)
                if in_draft:
                    spin.setValue(self._draft[barcode].quantity)
                else:
                    spin.setValue(0)
                spin.blockSignals(False)
                spin.setEnabled(not in_draft)

            if btn is not None:
                if in_draft:
                    btn.setEnabled(False)
                    btn.setText("✓  Στο πρόχειρο")
                else:
                    has_qty = spin is not None and spin.value() > 0
                    btn.setEnabled(has_qty)
                    btn.setText("➕  Προσθήκη")

    def _on_discard_clicked(self) -> None:
        """Clear the local draft.  Purely in-memory; touches nothing else."""
        self.clear_draft()
        self._state_lbl.hide()

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

    def _clear_candidates(self) -> None:
        """Remove all supplier/unassigned group boxes from the scroll area.

        Preserves the draft frame (first widget) and the trailing
        stretch item.  Active widgets are scheduled for deletion via
        deleteLater — never deleted synchronously.
        """
        # Layout order: [0] draft frame, [1..n-1] candidate group boxes,
        # [n-1] stretch.  Remove only the group boxes between them.
        while self._scroll_layout.count() > 2:
            # Index 1 is the first candidate widget (after draft frame).
            item = self._scroll_layout.takeAt(1)
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
