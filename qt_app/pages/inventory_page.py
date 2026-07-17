"""Inventory page — κατάλογος προϊόντων αποθήκης (read + write).

Uses QThread workers for both read (load_inventory_page) and write
(create/update via inventory_command_service).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QDate
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy, QDialog, QFormLayout,
    QSpinBox, QDoubleSpinBox, QDateEdit, QDialogButtonBox,
    QMessageBox,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_inventory_page, InventoryResult, load_supplier_choices,
    SupplierChoice,
)
from infrastructure.inventory_command_service import (
    CreateProductRequest, UpdateProductRequest, ProductSnapshot,
    CommandResult, create_product, update_product,
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
# QThread workers
# ═══════════════════════════════════════════════════════════════════════

class _InventoryWorker(QObject):
    finished = Signal(InventoryResult)
    def __init__(self, db_path, search_text, status_filter,
                 threshold, alert_days, page, page_size, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, status_filter, threshold,
                       alert_days, page, page_size)
    def run(self):
        self.finished.emit(load_inventory_page(*self._args))


class _WriteWorker(QObject):
    finished = Signal(CommandResult)
    def __init__(self, db_path, fn, req, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._fn = fn
        self._req = req
    def run(self):
        self.finished.emit(self._fn(self._db_path, self._req))


# ═══════════════════════════════════════════════════════════════════════
# Product dialog (create / edit)
# ═══════════════════════════════════════════════════════════════════════

class ProductDialog(QDialog):
    """Reusable Greek product form — no VAT fields."""

    def __init__(self, db_path: str, parent=None,
                 existing: dict | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._existing = existing
        self.setWindowTitle(
            "Επεξεργασία Προϊόντος" if existing else "Νέο Προϊόν")
        self.setMinimumWidth(450)

        lay = QFormLayout(self)

        # Barcode
        self._barcode_edit = QLineEdit()
        if existing:
            self._barcode_edit.setText(existing["barcode"])
            self._barcode_edit.setReadOnly(True)
            self._barcode_edit.setStyleSheet(
                f"color: {styles.TEXT_MUTED};")
        self._barcode_edit.setPlaceholderText("π.χ. 5201234567890")
        lay.addRow("Barcode:", self._barcode_edit)

        # Name
        self._name_edit = QLineEdit()
        if existing:
            self._name_edit.setText(existing["name"])
        self._name_edit.setPlaceholderText("π.χ. DEPON 500mg")
        lay.addRow("Όνομα Προϊόντος:", self._name_edit)

        # Stock
        self._stock_spin = QSpinBox()
        self._stock_spin.setRange(0, 999999)
        if existing:
            self._stock_spin.setValue(existing["stock"])
        lay.addRow("Απόθεμα:", self._stock_spin)

        # Expiry date
        self._expiry_edit = QDateEdit()
        self._expiry_edit.setCalendarPopup(True)
        self._expiry_edit.setDisplayFormat("yyyy-MM-dd")
        if existing:
            self._expiry_edit.setDate(
                QDate.fromString(existing["expiry_date"], "yyyy-MM-dd"))
        lay.addRow("Ημ. Λήξης:", self._expiry_edit)

        # Price
        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0, 999999.99)
        self._price_spin.setDecimals(2)
        self._price_spin.setPrefix("€ ")
        if existing:
            self._price_spin.setValue(existing["price"])
        lay.addRow("Τιμή (€):", self._price_spin)

        # Supplier
        self._supplier_combo = QComboBox()
        self._supplier_combo.addItem("—", None)
        for sc in load_supplier_choices(db_path):
            self._supplier_combo.addItem(sc.name, sc.id)
        if existing and existing.get("supplier_id"):
            for i in range(self._supplier_combo.count()):
                if self._supplier_combo.itemData(i) == existing["supplier_id"]:
                    self._supplier_combo.setCurrentIndex(i)
                    break
        lay.addRow("Προμηθευτής:", self._supplier_combo)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def _validate_and_accept(self):
        if not self._barcode_edit.text().strip():
            QMessageBox.warning(self, "Σφάλμα", "Το barcode είναι υποχρεωτικό.")
            return
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Σφάλμα", "Το όνομα είναι υποχρεωτικό.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "barcode": self._barcode_edit.text().strip(),
            "name": self._name_edit.text().strip(),
            "stock": self._stock_spin.value(),
            "expiry_date": self._expiry_edit.date().toString("yyyy-MM-dd"),
            "price": self._price_spin.value(),
            "supplier_id": self._supplier_combo.currentData(),
        }


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
        self._worker = None
        self._thread = None
        self._loading = False
        self._close_pending = False
        self._page = 1
        self._page_size = 50
        self._search_text = ""
        self._status_filter = "all"
        self._pending_req = None
        self._write_worker = None
        self._write_thread = None
        self._write_loading = False
        self._preview_dlg = None
        super().__init__(db_service, config, parent)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._do_refresh)
        self.refresh()

    # ── UI construction ──────────────────────────────────────────────
    def build_ui(self) -> None:
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText("Αναζήτηση barcode ή ονόματος…")
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
        self._refresh_btn.setStyleSheet(self._accent_btn_qss())
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)
        self.root_layout.addLayout(toolbar)

        # Action buttons row
        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self._create_btn = QPushButton("＋  Νέο Προϊόν")
        self._create_btn.setCursor(Qt.PointingHandCursor)
        self._create_btn.setStyleSheet(self._accent_btn_qss())
        self._create_btn.clicked.connect(self._on_create)
        act_row.addWidget(self._create_btn)
        self._edit_btn = QPushButton("✏️  Επεξεργασία Επιλεγμένου")
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.setStyleSheet(self._accent_btn_qss())
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_selected)
        act_row.addWidget(self._edit_btn)
        self._preview_btn = QPushButton("📥  Προεπισκόπηση Excel")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setStyleSheet(self._accent_btn_qss())
        self._preview_btn.clicked.connect(self._on_preview_xlsx)
        act_row.addWidget(self._preview_btn)
        act_row.addStretch()
        self.root_layout.addLayout(act_row)

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
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(self._on_double_click)
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

    # ── Button styling ───────────────────────────────────────────────
    @staticmethod
    def _accent_btn_qss() -> str:
        return (
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 8px 16px; "
            f"font-size: 13px; font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")

    # ── User actions ─────────────────────────────────────────────────
    def _on_search_changed(self, text):
        self._search_text = text
        self._page = 1
        self._debounce_timer.start(300)

    def _on_filter_changed(self, idx):
        self._status_filter = FILTER_KEYS[idx] if 0 <= idx < len(FILTER_KEYS) else "all"
        self._page = 1
        self._debounce_timer.stop()
        self._do_refresh()

    def _on_selection_changed(self):
        self._edit_btn.setEnabled(len(self._table.selectedItems()) > 0)

    def _on_double_click(self, row, col):
        self._edit_row(row)

    def _prev_page(self):
        if self._page > 1:
            self._page -= 1
            self._do_refresh()

    def _next_page(self):
        self._page += 1
        self._do_refresh()

    # ── Create / Edit ────────────────────────────────────────────────
    def _on_create(self):
        dlg = ProductDialog(self._db_path, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        req = CreateProductRequest(
            barcode=data["barcode"], name=data["name"],
            stock=data["stock"], expiry_date=data["expiry_date"],
            price=data["price"], supplier_id=data["supplier_id"])
        self._confirm_and_execute("create", req, data)

    def _on_preview_xlsx(self):
        from qt_app.dialogs.product_import_preview_dialog import (
            ProductImportPreviewDialog)
        dlg = ProductImportPreviewDialog(self, db_path=self._db_path)
        self._preview_dlg = dlg
        dlg.shutdown_ready.connect(self._on_preview_dlg_shutdown_ready)
        dlg.import_completed.connect(self._on_import_completed)
        dlg.exec()
        # Only clear ref if dialog is truly done (not deferred)
        if not dlg.is_busy():
            self._preview_dlg = None

    def _on_preview_dlg_shutdown_ready(self):
        if self._preview_dlg:
            self._preview_dlg = None
        if self._close_pending:
            self._maybe_finish_shutdown()

    def _on_import_completed(self, count):
        self.refresh()
        self._refresh_dashboard()

    def _on_edit_selected(self):
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        if len(rows) != 1:
            return
        self._edit_row(list(rows)[0])

    def _edit_row(self, row: int):
        sid = (self._row_supplier_ids[row]
               if hasattr(self, "_row_supplier_ids") and row < len(self._row_supplier_ids)
               else None)
        existing = {
            "barcode": self._table.item(row, 0).text(),
            "name": self._table.item(row, 1).text(),
            "stock": int(self._table.item(row, 2).text()),
            "expiry_date": self._table.item(row, 3).text(),
            "price": float(self._table.item(row, 4).text().replace("€", "").strip()),
            "supplier_id": sid,
        }
        dlg = ProductDialog(self._db_path, parent=self, existing=existing)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        original = ProductSnapshot(
            barcode=existing["barcode"], name=existing["name"],
            stock=existing["stock"], expiry_date=existing["expiry_date"],
            price=existing["price"], supplier_id=sid)
        req = UpdateProductRequest(
            barcode=data["barcode"], name=data["name"],
            stock=data["stock"], expiry_date=data["expiry_date"],
            price=data["price"], supplier_id=data["supplier_id"],
            original=original)
        self._confirm_and_execute("update", req, data)

    def _confirm_and_execute(self, mode, req, data):
        """Show confirmation, then dispatch write worker."""
        if mode == "create":
            summary = (
                f"Barcode: {req.barcode}\n"
                f"Όνομα: {req.name}\n"
                f"Απόθεμα: {req.stock}\n"
                f"Ημ. Λήξης: {req.expiry_date}\n"
                f"Τιμή: €{req.price:.2f}")
        else:
            summary = (
                f"Barcode: {req.barcode}\n"
                f"Όνομα: {req.name}\n"
                f"Απόθεμα: {req.stock}\n"
                f"Ημ. Λήξης: {req.expiry_date}\n"
                f"Τιμή: €{req.price:.2f}")

        warn = ""
        from datetime import date as dt_date
        try:
            if dt_date.fromisoformat(req.expiry_date) < dt_date.today():
                warn = ("\n\n⚠️  Προειδοποίηση: Η ημερομηνία λήξης είναι "
                        "στο παρελθόν.")
        except ValueError:
            pass

        title = "Επιβεβαίωση Δημιουργίας" if mode == "create" else "Επιβεβαίωση Ενημέρωσης"
        btn = QMessageBox.question(
            self, title, summary + warn,
            QMessageBox.Yes | QMessageBox.No)

        if btn != QMessageBox.Yes:
            return

        self._run_write(mode, req)

    def _run_write(self, mode, req):
        if self._write_loading:
            return
        self._write_loading = True
        self._create_btn.setEnabled(False)
        self._edit_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)

        fn = create_product if mode == "create" else update_product
        self._cleanup_write_worker()

        self._write_thread = QThread(self)
        self._write_worker = _WriteWorker(self._db_path, fn, req)
        self._write_worker.moveToThread(self._write_thread)
        self._write_thread.started.connect(self._write_worker.run)
        self._write_worker.finished.connect(self._on_write_done)
        self._write_worker.finished.connect(self._write_thread.quit)
        self._write_thread.finished.connect(self._write_worker.deleteLater)
        self._write_thread.finished.connect(self._write_thread.deleteLater)
        self._write_thread.finished.connect(self._on_write_thread_done)
        self._write_thread.start()

    def _on_write_done(self, result: CommandResult):
        if self._close_pending:
            return
        if result.ok:
            QMessageBox.information(self, "Επιτυχία", result.message)
            self.refresh()
            self._refresh_dashboard()
        else:
            QMessageBox.warning(self, "Σφάλμα", result.message)

    def _on_write_thread_done(self):
        """Write worker finished.  Clear own state; defer shutdown signalling."""
        self._write_loading = False
        self._write_worker = None
        self._write_thread = None
        if self._close_pending:
            self._maybe_finish_shutdown()
            return
        self._create_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)

    def _refresh_dashboard(self):
        """Refresh the Dashboard page if it has already been created."""
        mw = self.window()
        if mw and hasattr(mw, "_pages"):
            dash = mw._pages.get("dashboard")
            if dash and hasattr(dash, "refresh"):
                dash.refresh()

    # ── Refresh ──────────────────────────────────────────────────────
    def refresh(self):
        self._debounce_timer.stop()
        self._do_refresh()

    def _do_refresh(self):
        if self._loading:
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

    def _on_data_ready(self, result):
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
        self._summary_lbl.setText(f"Εμφάνιση {first}–{last} από {total} προϊόντα")
        prods = snap.products
        self._row_supplier_ids = [p.supplier_id for p in prods]
        if not prods:
            self._set_state("Δεν βρέθηκαν προϊόντα με τα επιλεγμένα φίλτρα.", styles.TEXT_MUTED)
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
                fg = QColor(styles.RED) if any("Ληγμένο" in s for s in p.status_labels) \
                    else QColor(styles.AMBER) if any(
                        "Λήγει" in s or "Χαμηλό" in s for s in p.status_labels) \
                    else QColor(styles.TEXT_PRIMARY)
                for c in range(len(self.COLUMNS)):
                    self._table.item(r, c).setForeground(fg)
        self._page = snap.page
        total_pages = max(1, (total + snap.page_size - 1) // snap.page_size)
        self._page_lbl.setText(f"Σελίδα {snap.page} από {total_pages}")
        self._prev_btn.setEnabled(snap.page > 1)
        self._next_btn.setEnabled(snap.page < total_pages)

    def _on_thread_done(self):
        """Read worker finished.  Clear own state; defer shutdown signalling."""
        self._loading = False
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._maybe_finish_shutdown()
            return
        self._refresh_btn.setEnabled(True)
        if self._pending_req:
            req = self._pending_req
            self._pending_req = None
            self._search_text = req["search"]
            self._status_filter = req["filter"]
            self._page = req["page"]
            self._do_refresh()

    # ── Shutdown ─────────────────────────────────────────────────────
    def _maybe_finish_shutdown(self):
        """If close is pending and no worker is still running, emit
        shutdown_ready exactly once."""
        if not self._close_pending:
            return
        read_running = self._thread is not None and self._thread.isRunning()
        write_running = self._write_thread is not None and self._write_thread.isRunning()
        preview_busy = (self._preview_dlg is not None
                        and self._preview_dlg.is_busy())
        if not read_running and not write_running and not preview_busy:
            self._close_pending = False
            self.shutdown_ready.emit()

    def _cleanup_worker(self):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def _cleanup_write_worker(self):
        if self._write_thread and self._write_thread.isRunning():
            self._write_thread.quit()
            self._write_thread.wait(2000)
        self._write_worker = None
        self._write_thread = None

    def shutdown(self) -> bool:
        """Return True only when ALL workers have stopped.
        Preserves references on timeout — never clears a running QThread."""
        # Cancel active preview dialog
        if self._preview_dlg is not None:
            if self._preview_dlg.is_busy():
                self._close_pending = True
                self._preview_dlg.request_shutdown()
                return False
            # idle — close it and continue
            self._preview_dlg.close()
            self._preview_dlg = None

        all_stopped = True

        # Attempt write worker shutdown
        if self._write_thread and self._write_thread.isRunning():
            try:
                self._write_worker.finished.disconnect(self._on_write_done)
            except (RuntimeError, TypeError):
                pass
            self._close_pending = True
            self._write_thread.quit()
            if self._write_thread.wait(2000):
                self._write_worker = None
                self._write_thread = None
                self._write_loading = False
            else:
                all_stopped = False

        # Attempt read worker shutdown
        if self._thread and self._thread.isRunning():
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
            else:
                all_stopped = False

        if all_stopped:
            self._close_pending = False
        return all_stopped

    # ── Helpers ─────────────────────────────────────────────────────
    def _set_state(self, text, color):
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(f"color: {color}; font-size: 14px; padding: 20px;")
        self._state_lbl.show()
        self._table.hide()

    def _clear_table(self):
        self._table.setRowCount(0)
