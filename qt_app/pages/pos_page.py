"""POS page — σημείο πώλησης Phase A (δοκιμαστικό καλάθι, χωρίς αποθήκευση)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from qt_app.data_source import (
    load_pos_catalog_page, POSCatalogResult, POSProduct,
    preflight_pos_sale, POSPreflightResult,
)


class _POSCatalogWorker(QObject):
    finished = Signal(object)  # POSCatalogResult

    def __init__(self, db_path, search_text, page, page_size, parent=None):
        super().__init__(parent)
        self._args = (db_path, search_text, page, page_size)

    def run(self):
        self.finished.emit(load_pos_catalog_page(*self._args))


class _POSPreflightWorker(QObject):
    finished = Signal(object)  # POSPreflightResult

    def __init__(self, db_path, cart_lines, parent=None):
        super().__init__(parent)
        self._db = db_path
        self._lines = cart_lines

    def run(self):
        self.finished.emit(preflight_pos_sale(self._db, self._lines))


class POSPage(BasePage):
    shutdown_ready = Signal()

    CAT_COLS = ["Barcode", "Προϊόν", "Διαθέσιμο", "Τιμή"]
    CART_COLS = ["Προϊόν", "Τιμή", "Ποσότητα", "Σύνολο"]

    @classmethod
    def page_title(cls) -> str:
        return "Ταμείο / Πωλήσεις"

    def __init__(self, db_service, config, parent=None):
        self._db_path = (config.get("db_path", "encomm_erp.db")
                         if config else "encomm_erp.db")
        self._worker = None
        self._thread = None
        self._loading = False
        self._close_pending = False
        self._page = 1
        self._page_size = 50
        self._cart: dict[str, dict] = {}  # barcode → {name, price, qty, max_stock}
        self._mode = "catalog"  # "catalog" | "preflight"
        self._preflight_status = ""
        super().__init__(db_service, config, parent)

    def build_ui(self):
        main = QHBoxLayout()
        main.setSpacing(16)

        # ── LEFT: catalog ──
        left = QVBoxLayout()
        left.setSpacing(8)

        tb = QHBoxLayout()
        tb.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Αναζήτηση barcode ή προϊόντος…")
        self._search.setMinimumHeight(36)
        self._search.returnPressed.connect(self.refresh)
        tb.addWidget(self._search, 3)
        self._refresh_btn = QPushButton("🔄  Ανανέωση")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(self._btn_qss())
        self._refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(self._refresh_btn)
        left.addLayout(tb)

        self._add_btn = QPushButton("➕  Προσθήκη στο καλάθι")
        self._add_btn.setCursor(Qt.PointingHandCursor)
        self._add_btn.setStyleSheet(self._btn_qss())
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._on_add_to_cart)
        left.addWidget(self._add_btn)

        self._state_lbl = QLabel("")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setAlignment(Qt.AlignCenter)
        self._state_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 14px; padding: 20px;")
        left.addWidget(self._state_lbl)

        self._cat_table = QTableWidget(0, len(self.CAT_COLS))
        self._cat_table.setHorizontalHeaderLabels(self.CAT_COLS)
        ch = self._cat_table.horizontalHeader()
        ch.setStretchLastSection(True)
        for i in range(len(self.CAT_COLS)):
            ch.setSectionResizeMode(i, QHeaderView.Stretch)
        self._cat_table.verticalHeader().setVisible(False)
        self._cat_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._cat_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._cat_table.itemSelectionChanged.connect(
            lambda: self._add_btn.setEnabled(
                len(self._cat_table.selectedItems()) > 0))
        self._cat_table.cellDoubleClicked.connect(lambda r, c: self._on_add_to_cart())
        left.addWidget(self._cat_table, 1)

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
        left.addLayout(pg)
        main.addLayout(left, 3)

        # ── RIGHT: cart ──
        right = QVBoxLayout()
        right.setSpacing(8)

        cart_title = QLabel("<b>Καλάθι</b>")
        cart_title.setStyleSheet(f"color: {styles.TEXT_PRIMARY}; font-size: 16px;")
        right.addWidget(cart_title)

        self._cart_table = QTableWidget(0, len(self.CART_COLS))
        self._cart_table.setHorizontalHeaderLabels(self.CART_COLS)
        ch2 = self._cart_table.horizontalHeader()
        ch2.setStretchLastSection(True)
        for i in range(len(self.CART_COLS)):
            ch2.setSectionResizeMode(i, QHeaderView.Stretch)
        self._cart_table.verticalHeader().setVisible(False)
        self._cart_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._cart_table.setEditTriggers(QTableWidget.NoEditTriggers)
        right.addWidget(self._cart_table, 1)

        # Cart controls
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self._cart_plus = QPushButton("＋")
        self._cart_plus.setMaximumWidth(40)
        self._cart_plus.setStyleSheet(self._btn_qss())
        self._cart_plus.clicked.connect(lambda: self._modify_cart(1))
        ctl.addWidget(self._cart_plus)
        self._cart_minus = QPushButton("－")
        self._cart_minus.setMaximumWidth(40)
        self._cart_minus.setStyleSheet(self._btn_qss())
        self._cart_minus.clicked.connect(lambda: self._modify_cart(-1))
        ctl.addWidget(self._cart_minus)
        self._cart_remove = QPushButton("Αφαίρεση")
        self._cart_remove.setStyleSheet(self._btn_qss())
        self._cart_remove.clicked.connect(self._remove_cart_line)
        ctl.addWidget(self._cart_remove)
        self._cart_clear = QPushButton("Καθαρισμός")
        self._cart_clear.setStyleSheet(self._btn_qss())
        self._cart_clear.clicked.connect(self._clear_cart)
        ctl.addWidget(self._cart_clear)
        ctl.addStretch()
        right.addLayout(ctl)

        # Preflight check
        self._preflight_btn = QPushButton("🔍  Έλεγχος καλαθιού")
        self._preflight_btn.setCursor(Qt.PointingHandCursor)
        self._preflight_btn.setStyleSheet(self._btn_qss())
        self._preflight_btn.setEnabled(False)
        self._preflight_btn.clicked.connect(self._on_preflight)
        right.addWidget(self._preflight_btn)

        self._preflight_lbl = QLabel("")
        self._preflight_lbl.setWordWrap(True)
        self._preflight_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px; padding: 4px 0;")
        right.addWidget(self._preflight_lbl)

        # Totals
        self._total_items = QLabel("Είδη: 0")
        self._total_items.setStyleSheet(f"color: {styles.TEXT_PRIMARY}; font-size: 14px;")
        right.addWidget(self._total_items)
        self._total_price = QLabel("Σύνολο: €0.00")
        self._total_price.setStyleSheet(f"color: {styles.ACCENT}; font-size: 18px; font-weight: bold;")
        right.addWidget(self._total_price)

        self._complete_btn = QPushButton("Ολοκλήρωση πώλησης — Σύντομα")
        self._complete_btn.setEnabled(False)
        self._complete_btn.setStyleSheet(
            f"QPushButton {{ background: #3b3f48; color: #6b7280; "
            f"border-radius: 6px; padding: 10px; font-size: 14px; font-weight: bold; }}")
        right.addWidget(self._complete_btn)

        notice = QLabel("⚠ Το καλάθι είναι δοκιμαστικό — δεν δημιουργεί παραστατικό.")
        notice.setWordWrap(True)
        notice.setStyleSheet(f"color: {styles.AMBER}; font-size: 11px;")
        right.addWidget(notice)

        main.addLayout(right, 2)
        self.root_layout.addLayout(main)
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

    # ── Catalog ──
    def _go_page(self, delta):
        self._page += delta
        self._do_refresh()

    def refresh(self):
        self._page = 1
        self._do_refresh()

    def _do_refresh(self):
        if self._loading:
            return
        self._mode = "catalog"
        self._loading = True
        self._set_controls_loading(True)
        self._set_state("🔄 Φόρτωση καταλόγου...", styles.TEXT_MUTED)
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _POSCatalogWorker(
            self._db_path, self._search.text().strip(),
            self._page, self._page_size)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_done)
        self._thread.start()

    def _set_controls_loading(self, loading: bool):
        """Enable/disable all catalog + cart controls in one place."""
        self._search.setEnabled(not loading)
        self._refresh_btn.setEnabled(not loading)
        self._add_btn.setEnabled(False)
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        if loading:
            self._page_lbl.setText("Φόρτωση…")
        self._preflight_btn.setEnabled(
            not loading and len(self._cart) > 0)
        # Disable cart mutation during preflight
        if self._mode == "preflight":
            for w in [self._cart_plus, self._cart_minus,
                      self._cart_remove, self._cart_clear]:
                w.setEnabled(not loading)

    def _on_ready(self, result):
        if self._close_pending:
            return
        if self._mode == "preflight":
            self._on_preflight_ready(result)
            return
        # Catalog result
        r = result
        if not isinstance(r, POSCatalogResult):
            return
        if not r.ok:
            self._set_state(r.error_message, styles.RED)
            self._cat_table.setRowCount(0)
            self._page = 1
            self._page_lbl.setText("Σελίδα 1")
            return
        prods = r.products
        if not prods:
            self._set_state("Δεν βρέθηκαν διαθέσιμα προϊόντα.", styles.TEXT_MUTED)
            self._cat_table.setRowCount(0)
            self._page = r.page
            tp = max(1, (r.total + r.page_size - 1) // r.page_size)
            self._page_lbl.setText(f"Σελίδα {r.page} από {tp}")
        else:
            self._state_lbl.hide()
            self._cat_table.show()
            self._cat_table.setRowCount(len(prods))
            for ri, p in enumerate(prods):
                b_item = QTableWidgetItem(p.barcode)
                b_item.setData(Qt.UserRole, p.barcode)
                self._cat_table.setItem(ri, 0, b_item)
                self._cat_table.setItem(ri, 1, QTableWidgetItem(p.name))
                self._cat_table.setItem(ri, 2, QTableWidgetItem(str(p.stock)))
                self._cat_table.setItem(ri, 3, QTableWidgetItem(f"€{p.price:.2f}"))
        self._page = r.page
        tp = max(1, (r.total + r.page_size - 1) // r.page_size)
        self._page_lbl.setText(f"Σελίδα {r.page} από {tp}")
        self._prev_btn.setEnabled(r.page > 1)
        self._next_btn.setEnabled(r.page < tp)

    def _on_done(self):
        self._loading = False
        self._mode = "catalog"
        self._search.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        for w in [self._cart_plus, self._cart_minus,
                  self._cart_remove, self._cart_clear]:
            w.setEnabled(True)
        self._preflight_btn.setEnabled(len(self._cart) > 0)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()

    # ── Cart ──
    def _on_add_to_cart(self):
        rows = {it.row() for it in self._cat_table.selectedItems()}
        if len(rows) != 1:
            return
        r = list(rows)[0]
        barcode = self._cat_table.item(r, 0).data(Qt.UserRole)
        name = self._cat_table.item(r, 1).text()
        price = float(self._cat_table.item(r, 3).text().replace("€", "").strip())
        stock = int(self._cat_table.item(r, 2).text())

        if barcode in self._cart:
            entry = self._cart[barcode]
            if entry["qty"] + 1 > entry["max_stock"]:
                QMessageBox.warning(self, "Όριο αποθέματος",
                    f"Το διαθέσιμο απόθεμα για το '{name}' είναι {entry['max_stock']}.")
                return
            entry["qty"] += 1
        else:
            if stock <= 0:
                return
            self._cart[barcode] = {"name": name, "price": price, "qty": 1, "max_stock": stock}
        self._rebuild_cart()

    def _modify_cart(self, delta):
        barcode = self._selected_cart_barcode()
        if not barcode or barcode not in self._cart:
            return
        entry = self._cart[barcode]
        new_qty = entry["qty"] + delta
        if new_qty < 1:
            del self._cart[barcode]
        elif new_qty > entry["max_stock"]:
            QMessageBox.warning(self, "Όριο αποθέματος",
                f"Το διαθέσιμο απόθεμα είναι {entry['max_stock']}.")
        else:
            entry["qty"] = new_qty
        self._rebuild_cart()

    def _remove_cart_line(self):
        barcode = self._selected_cart_barcode()
        if barcode and barcode in self._cart:
            del self._cart[barcode]
        self._rebuild_cart()

    def _clear_cart(self):
        self._cart.clear()
        self._rebuild_cart()

    # ── Preflight ──
    def _on_preflight(self):
        if self._loading or not self._cart:
            return
        lines = [(bc, e["qty"]) for bc, e in self._cart.items()]
        self._mode = "preflight"
        self._loading = True
        self._set_controls_loading(True)
        self._preflight_lbl.setText("🔄 Έλεγχος καλαθιού…")
        self._preflight_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 12px; padding: 4px 0;")
        self._cleanup_worker()
        self._thread = QThread(self)
        self._worker = _POSPreflightWorker(self._db_path, lines)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_ready)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_done)
        self._thread.start()

    def _on_preflight_ready(self, result: POSPreflightResult):
        if not isinstance(result, POSPreflightResult):
            return
        if result.ok and result.lines:
            msgs = ["✅ Ο προέλεγχος ολοκληρώθηκε — δεν πραγματοποιήθηκε πώληση."]
            # Price warnings
            for line in result.lines:
                cart_entry = self._cart.get(line.barcode)
                if cart_entry and line.valid and line.current_price != cart_entry["price"]:
                    msgs.append(
                        f"⚠ Το προϊόν '{line.name}' έχει νέα τιμή: "
                        f"€{line.current_price:.2f} (ήταν €{cart_entry['price']:.2f}).")
            msgs.append(f"Σύνολο καλαθιού (εξουσιοδοτημένη τιμή): €{result.gross_total:.2f}")
            self._preflight_lbl.setText("\n".join(msgs))
            self._preflight_lbl.setStyleSheet(
                f"color: {styles.ACCENT}; font-size: 12px; padding: 4px 0;")
        else:
            if result.error_message and not result.lines:
                msgs = [f"❌ {result.error_message}"]
            else:
                msgs = ["❌ Προβλήματα προελέγχου:"]
                for line in result.lines:
                    if not line.valid:
                        msgs.append(f"  · {line.barcode}: {line.error_message}")
            self._preflight_lbl.setText("\n".join(msgs))
            self._preflight_lbl.setStyleSheet(
                f"color: {styles.RED}; font-size: 12px; padding: 4px 0;")

    def _selected_cart_barcode(self):
        rows = {it.row() for it in self._cart_table.selectedItems()}
        if len(rows) == 1:
            r = list(rows)[0]
            return self._cart_table.item(r, 0).data(Qt.UserRole)
        return None

    def _rebuild_cart(self):
        self._cart_table.setRowCount(len(self._cart))
        total_items = 0
        total_price = 0.0
        for r, (bc, entry) in enumerate(sorted(self._cart.items())):
            name_item = QTableWidgetItem(entry["name"])
            name_item.setData(Qt.UserRole, bc)
            self._cart_table.setItem(r, 0, name_item)
            self._cart_table.setItem(r, 1, QTableWidgetItem(f"€{entry['price']:.2f}"))
            self._cart_table.setItem(r, 2, QTableWidgetItem(str(entry["qty"])))
            line = entry["qty"] * entry["price"]
            self._cart_table.setItem(r, 3, QTableWidgetItem(f"€{line:.2f}"))
            total_items += entry["qty"]
            total_price += line
        self._total_items.setText(f"Είδη: {total_items}")
        self._total_price.setText(f"Σύνολο: €{total_price:.2f}")
        self._preflight_btn.setEnabled(total_items > 0 and not self._loading)
        self._preflight_status = ""
        self._preflight_lbl.setText("")

    # ── Shutdown ──
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
        self._cat_table.hide()
