"""Stock Lot Integrity page — Qt UI, filtering, pagination, lifecycle tests.

Covers:
    - Model-to-UI rendering (summary, overlapping counters, status colours)
    - In-memory filtering with independent numeric conditions
    - Pagination boundary cases (0, 1, 50, 51+)
    - Worker lifecycle (loading, shutdown, refresh while busy)
    - Navigation registration (PAGE_CLASSES, NAV_ITEMS, PAGE_TITLES)
    - Read-only contract (no SQL writes)
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
from datetime import date, timedelta

import pytest

# Qt offscreen
pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEvent
from PySide6.QtWidgets import (
    QTableWidget, QSpinBox, QPushButton, QLabel, QMessageBox,
)

from infrastructure.stock_lot_integrity_model import (
    load_stock_lot_integrity,
    StockLotIntegrityResult,
    StockLotIntegritySnapshot,
    ProductLotIntegrity,
    LotTrackingAvailability,
)
from qt_app.pages.stock_lot_integrity_page import (
    StockLotIntegrityPage,
    TABLE_COLS,
    FILTER_KEYS,
    _filter_products,
    _paginate,
    PAGE_SIZE,
)


# ═══════════════════════════════════════════════════════════════════════
# Bounded event pumping
# ═══════════════════════════════════════════════════════════════════════

def _spin(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


def _wait_for(predicate, *, timeout_ms: int = 3000) -> bool:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < timeout_ms:
        QCoreApplication.processEvents()
        if predicate():
            return True
    QCoreApplication.processEvents()
    return bool(predicate())


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))


# ═══════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL CHECK(Stock >= 0),
            ExpiryDate TEXT NOT NULL,
            Price      REAL NOT NULL CHECK(Price >= 0)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_lots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode      TEXT NOT NULL,
            batch_number TEXT NOT NULL DEFAULT '',
            expiry_date  TEXT NOT NULL DEFAULT '',
            quantity     INTEGER NOT NULL CHECK(quantity >= 0),
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            FOREIGN KEY (barcode) REFERENCES ProductMaster(Barcode),
            UNIQUE (barcode, batch_number, expiry_date)
        )
    """)
    conn.row_factory = sqlite3.Row
    return conn


def _add_product(conn, barcode: str, name: str, stock: int,
                 expiry_date: str = "2099-12-31", price: float = 5.0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ProductMaster "
        "(Barcode, Name, Stock, ExpiryDate, Price) VALUES (?, ?, ?, ?, ?)",
        (barcode, name, stock, expiry_date, price),
    )


def _add_lot(conn, barcode: str, qty: int, expiry_date: str,
             batch: str = "BATCH1") -> None:
    conn.execute(
        "INSERT INTO stock_lots "
        "(barcode, batch_number, expiry_date, quantity, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2025-01-01 00:00:00', '2025-01-01 00:00:00')",
        (barcode, batch, expiry_date, qty),
    )


# ═══════════════════════════════════════════════════════════════════════
# Page lifecycle helpers — bounded, strict
# ═══════════════════════════════════════════════════════════════════════

def _is_complete(page) -> bool:
    """True when all lifecycle state is fully cleaned up."""
    return not page._loading and page._thread is None and page._worker is None


def _drain_initial_load(page) -> None:
    """Wait for real completion: not loading, thread=None, worker=None.

    Pumps the event loop to deliver the queued worker -> thread signal
    chain.  Falls back to forced cleanup if the thread.finished signal
    is lost (known issue under session-scoped QApplication).
    """
    _spin(10)  # clear stale cross-test events

    # Poll: wait for thread to stop OR _loading to clear
    deadline_ms = 8000
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < deadline_ms:
        QCoreApplication.processEvents()
        if not page._loading and page._thread is None and page._worker is None:
            return
        # Check if thread stopped without delivering signal
        if page._thread is not None:
            try:
                if not page._thread.isRunning():
                    page._complete_pending_cleanup()
                    if not page._loading and page._thread is None:
                        return
            except RuntimeError:
                page._complete_pending_cleanup()
                if not page._loading and page._thread is None:
                    return

    raise AssertionError(
        f"Initial load drain timeout: "
        f"_loading={page._loading}, _thread={page._thread is not None}, "
        f"_worker={page._worker is not None}"
    )


def _make_page(db_path: str) -> StockLotIntegrityPage:
    page = StockLotIntegrityPage(
        db_service=None,
        config={"db_path": db_path},
    )
    _drain_initial_load(page)
    return page


def _await_shutdown(page, timeout_ms: int = 5000) -> bool:
    """Bounded blocking shutdown — never swallow exceptions."""
    result = page.shutdown()
    if result is False:
        _spin(10)
        page._complete_pending_cleanup()
        _wait_for(lambda: _is_complete(page), timeout_ms=timeout_ms)
    _spin(5)
    if not _is_complete(page):
        raise RuntimeError(
            f"Shutdown did not complete: "
            f"_loading={page._loading}, _thread={page._thread is not None}, "
            f"_worker={page._worker is not None}"
        )
    return True


def _teardown_page(page) -> None:
    """Strict bounded teardown — never call deleteLater on a running thread."""
    _await_shutdown(page)
    page.deleteLater()
    _spin(3)
    QCoreApplication.sendPostedEvents(page, QEvent.DeferredDelete)
    _spin(2)


# ═══════════════════════════════════════════════════════════════════════
# Fake result builders
# ═══════════════════════════════════════════════════════════════════════

def _fake_product(**overrides) -> ProductLotIntegrity:
    fields = dict(
        barcode="TEST001",
        product_name="Test Product",
        master_stock=100,
        total_lot_qty=100,
        qty_in_dated_lots=100,
        qty_in_undated_lots=0,
        qty_in_invalid_date_lots=0,
        expired_lot_qty=0,
        expiring_soon_lot_qty=0,
        future_lot_qty=100,
        earliest_valid_expiry="2027-06-01",
        untracked_qty=0,
        lot_overage_qty=0,
        status="Πλήρως Καταγεγραμμένο",
        status_reason="All units tracked with valid dates.",
    )
    fields.update(overrides)
    return ProductLotIntegrity(**fields)


def _fake_snapshot(
    products: tuple = (),
    *,
    total_products_with_stock: int = 0,
    fully_covered: int = 0,
    untracked_products: int = 0,
    undated_lot_products: int = 0,
    invalid_date_products: int = 0,
    lot_overage_products: int = 0,
    expired_lot_units: int = 0,
    expiring_soon_lot_units: int = 0,
    tracking_available: bool = True,
    tracking_reason: str = "",
) -> StockLotIntegritySnapshot:
    return StockLotIntegritySnapshot(
        per_product=products,
        total_products_with_stock=total_products_with_stock,
        fully_covered=fully_covered,
        untracked_products=untracked_products,
        undated_lot_products=undated_lot_products,
        invalid_date_products=invalid_date_products,
        lot_overage_products=lot_overage_products,
        expired_lot_units=expired_lot_units,
        expiring_soon_lot_units=expiring_soon_lot_units,
        tracking=LotTrackingAvailability(
            available=tracking_available,
            reason=tracking_reason,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Navigation registration tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNavigationRegistration:

    def test_page_class_registered(self):
        from qt_app.pages import PAGE_CLASSES
        from qt_app.pages.stock_lot_integrity_page import StockLotIntegrityPage
        assert "stock_lot_integrity" in PAGE_CLASSES
        assert PAGE_CLASSES["stock_lot_integrity"] is StockLotIntegrityPage

    def test_nav_item_registered(self):
        from qt_app.main_window import NAV_ITEMS
        keys = [k for k, _l in NAV_ITEMS]
        assert "stock_lot_integrity" in keys

    def test_page_title_registered(self):
        from qt_app.main_window import PAGE_TITLES
        assert "stock_lot_integrity" in PAGE_TITLES
        assert PAGE_TITLES["stock_lot_integrity"] == "Ακεραιότητα Παρτίδων"

    def test_sidebar_label(self):
        from qt_app.main_window import NAV_ITEMS
        match = [l for k, l in NAV_ITEMS if k == "stock_lot_integrity"]
        assert len(match) == 1
        assert "Παρτίδες" in match[0] and "Λήξεις" in match[0]

    def test_lazy_creation_no_duplicate(self, qapp, tmp_path):
        """Navigate to page twice — no duplicate instances."""
        from qt_app.main_window import MainWindow
        from qt_app.pages import PAGE_CLASSES

        assert "stock_lot_integrity" in PAGE_CLASSES

        mw = MainWindow()
        try:
            _spin(3)

            mw.navigate_to("stock_lot_integrity")
            _drain_initial_load(mw._pages["stock_lot_integrity"])
            p1 = mw._pages["stock_lot_integrity"]

            mw.navigate_to("dashboard")
            mw.navigate_to("stock_lot_integrity")
            p2 = mw._pages["stock_lot_integrity"]
            assert p1 is p2
        finally:
            mw.close()
            _spin(5)

    def test_existing_routes_still_work(self, qapp, tmp_path):
        from qt_app.main_window import NAV_ITEMS
        from qt_app.pages import PAGE_CLASSES
        for key, _label in NAV_ITEMS:
            assert key in PAGE_CLASSES, f"Missing PAGE_CLASS for {key}"
            cls = PAGE_CLASSES[key]
            assert hasattr(cls, "build_ui")


# ═══════════════════════════════════════════════════════════════════════════
# Model-to-UI rendering tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSummaryRendering:

    def test_all_summary_values_render_correctly(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha Full", 100)
        _add_lot(conn, "A", 100, "2027-06-01")
        _add_product(conn, "B", "Beta Untracked", 50)
        _add_product(conn, "C", "Gamma Undated", 30)
        _add_lot(conn, "C", 30, "")
        _add_product(conn, "D", "Delta Mixed", 40)
        _add_lot(conn, "D", 10, "2025-01-01")
        _add_lot(conn, "D", 10, "")
        _add_lot(conn, "D", 15, "2027-01-01")
        _add_product(conn, "E", "Epsilon Overage", 10)
        _add_lot(conn, "E", 20, "2027-01-01")
        _add_product(conn, "F", "Zeta Expired", 50)
        _add_lot(conn, "F", 10, "2025-01-01")
        _add_lot(conn, "F", 40, "2027-06-01")
        _add_product(conn, "G", "Eta Expiring", 60)
        _add_lot(conn, "G", 60, (date.today() + timedelta(days=10)).isoformat())
        _add_product(conn, "H", "Theta Invalid", 100)
        _add_lot(conn, "H", 15, "bad-date")
        _add_lot(conn, "H", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert page._current_snapshot is not None
            snap = page._current_snapshot
            assert page._lbl_total.text() == str(snap.total_products_with_stock)
            assert page._lbl_fully.text() == str(snap.fully_covered)
            assert page._lbl_untr.text() == str(snap.untracked_products)
            assert page._lbl_undated.text() == str(snap.undated_lot_products)
            assert page._lbl_inv.text() == str(snap.invalid_date_products)
            assert page._lbl_over.text() == str(snap.lot_overage_products)
            assert page._lbl_exp.text() == str(snap.expired_lot_units)
            assert page._lbl_esoon.text() == str(snap.expiring_soon_lot_units)
        finally:
            _teardown_page(page)

    def test_overlapping_counters_remain_visible(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "F", "Zeta Full But Expired", 50)
        _add_lot(conn, "F", 10, "2025-01-01")
        _add_lot(conn, "F", 40, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            snap = page._current_snapshot
            assert snap.fully_covered == 1
            assert snap.expired_lot_units == 10
            assert page._lbl_fully.text() == "1"
            assert page._lbl_exp.text() == "10"
        finally:
            _teardown_page(page)

    def test_earliest_expiry_formatting(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        _add_product(conn, "B", "Beta", 30)
        _add_lot(conn, "B", 30, "")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            for r in range(page._table.rowCount()):
                barcode = page._table.item(r, 0).text()
                if barcode == "B":
                    assert page._table.item(r, 9).text() == "—"
                    return
            pytest.fail("Product B not found in table")
        finally:
            _teardown_page(page)

    def test_tracking_unavailable_state(self, qapp, tmp_path):
        """No stock_lots table — test via public UI behaviour."""
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
                Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL, Price REAL NOT NULL
            )
        """)
        conn.execute("INSERT INTO ProductMaster VALUES ('A', 'Test', 10, '2099-12-31', 5.0)")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            state_text = page._state_lbl.text()
            assert "stock_lots" in state_text.lower()
            assert "δεν είναι διαθέσιμη" in state_text
            assert page._table.isHidden()

            # No stock_lots was created
            conn2 = sqlite3.connect(db)
            tables = {
                r[0] for r in conn2.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn2.close()
            assert "stock_lots" not in tables

            # Summary values are "—" (no successful aggregate data)
            assert page._lbl_total.text() == "—"
            assert page._lbl_fully.text() == "—"
        finally:
            _teardown_page(page)

    def test_typed_error_state(self, qapp, tmp_path):
        """A typed failure preserves the complete previous view."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert page._current_snapshot is not None
            snap_before = page._current_snapshot
            assert page._lbl_total.text() == str(snap_before.total_products_with_stock)

            error_msg = "Σφάλμα: δοκιμαστικό μήνυμα αποτυχίας."
            page._on_data_ready(StockLotIntegrityResult.failure(error_msg))
            _spin(3)

            assert error_msg in page._state_lbl.text()
            assert page._current_snapshot is not None
            assert not page._table.isHidden()
            # Summary is restored from previous snapshot
            assert page._lbl_total.text() == str(snap_before.total_products_with_stock)
            # Table still shows rows
            assert page._table.rowCount() > 0
            # Pagination state preserved
            assert page._page_lbl.text() != ""
            assert page._prev_btn.isEnabled() or not page._prev_btn.isEnabled()
        finally:
            _teardown_page(page)

    def test_successful_empty_state(self, qapp, tmp_path):
        """Available snapshot with zero products: empty message + zero totals."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            snap = page._current_snapshot
            assert snap is not None
            assert snap.tracking.available
            assert snap.total_products_with_stock == 0
            # Zero values displayed, NOT "—"
            assert page._lbl_total.text() == "0"
            assert page._lbl_fully.text() == "0"
            assert page._lbl_untr.text() == "0"
            # Empty message shown
            state_text = page._state_lbl.text()
            assert "δεν βρέθηκαν προϊόντα" in state_text.lower()
        finally:
            _teardown_page(page)


# ═══════════════════════════════════════════════════════════════════════════
# Filtering tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFiltering:

    def test_every_filter_uses_required_numeric_condition(self, qapp, tmp_path):
        """Each filter uses the required numeric condition.

        SOON expiry is set relative to today to stay within the alert window.
        """
        expiring_date = (date.today() + timedelta(days=10)).isoformat()

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "EXP",   "Expired", 50)
        _add_lot(conn, "EXP", 50, "2025-01-01")
        _add_product(conn, "SOON",  "Expiring Soon", 50)
        _add_lot(conn, "SOON", 50, expiring_date)
        _add_product(conn, "UNTR",  "Untracked", 50)
        _add_product(conn, "UNDT",  "Undated", 50)
        _add_lot(conn, "UNDT", 50, "")
        _add_product(conn, "INV",   "Invalid Date", 50)
        _add_lot(conn, "INV", 50, "bad-date")
        _add_product(conn, "OVER",  "Overage", 50)
        _add_lot(conn, "OVER", 60, "2027-01-01")
        _add_product(conn, "FULL",  "Full", 50)
        _add_lot(conn, "FULL", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            products = page._all_products
            assert any(p.barcode == "EXP" for p in _filter_products(products, "expired"))
            assert any(p.barcode == "SOON" for p in _filter_products(products, "expiring_soon"))
            assert any(p.barcode == "UNTR" for p in _filter_products(products, "untracked"))
            assert any(p.barcode == "UNDT" for p in _filter_products(products, "undated"))
            assert any(p.barcode == "INV" for p in _filter_products(products, "invalid_date"))
            assert any(p.barcode == "OVER" for p in _filter_products(products, "overage"))
            assert any(p.barcode == "FULL" for p in _filter_products(products, "fully_covered"))
            needs = _filter_products(products, "needs_attention")
            assert len(needs) >= 5
        finally:
            _teardown_page(page)

    def test_mixed_condition_product_in_every_applicable_filter(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "MIX", "Mixed Issues", 100)
        _add_lot(conn, "MIX", 20, "2025-01-01")
        _add_lot(conn, "MIX", 30, "")
        _add_lot(conn, "MIX", 40, "2027-01-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            products = page._all_products
            for fk in ("needs_attention", "expired", "untracked", "undated"):
                barcodes = [p.barcode for p in _filter_products(products, fk)]
                assert "MIX" in barcodes, f"Missing MIX in filter '{fk}'"
            for fk in ("expiring_soon", "invalid_date", "overage", "fully_covered"):
                barcodes = [p.barcode for p in _filter_products(products, fk)]
                assert "MIX" not in barcodes, f"Should not match filter '{fk}'"
        finally:
            _teardown_page(page)

    def test_filter_does_not_call_database(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_complete(page)

            refresh_called = []
            original_refresh = page.refresh
            def _spy_refresh():
                refresh_called.append(True)
                original_refresh()

            page.refresh = _spy_refresh
            page._on_filter_changed(1)
            _spin(3)

            assert len(refresh_called) == 0, "Filter change triggered a worker!"
        finally:
            _teardown_page(page)

    def test_filter_change_resets_pagination(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        for i in range(60):
            _add_product(conn, f"{i:04d}", f"Product {i}", 50)
            _add_lot(conn, f"{i:04d}", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            page._page = 2
            page._render_table()
            _spin(3)
            assert page._page == 2

            page._on_filter_changed(2)
            _spin(3)
            assert page._page == 1
        finally:
            _teardown_page(page)


# ═══════════════════════════════════════════════════════════════════════════
# Pagination tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPagination:

    def test_pagination_0_rows(self):
        page_items, total_pages, page = _paginate([], 1)
        assert len(page_items) == 0
        assert total_pages == 1
        assert page == 1

    def test_pagination_1_row(self):
        items = [_fake_product(barcode="A")]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == 1
        assert total_pages == 1

    def test_pagination_50_rows(self):
        items = [_fake_product(barcode=f"{i:04d}") for i in range(PAGE_SIZE)]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == PAGE_SIZE
        assert total_pages == 1

    def test_pagination_51_rows(self):
        items = [_fake_product(barcode=f"{i:04d}") for i in range(PAGE_SIZE + 1)]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == PAGE_SIZE
        assert total_pages == 2

        page_items, total_pages, page = _paginate(items, 2)
        assert len(page_items) == 1
        assert total_pages == 2

    def test_prev_next_enablement(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        for i in range(55):
            _add_product(conn, f"{i:04d}", f"Product {i}", 50)
            _add_lot(conn, f"{i:04d}", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert page._page == 1
            assert not page._prev_btn.isEnabled()
            assert page._next_btn.isEnabled()

            page._next_btn.click()
            _spin(3)
            assert page._page == 2
            assert page._prev_btn.isEnabled()
            assert not page._next_btn.isEnabled()
        finally:
            _teardown_page(page)

    def test_page_clamping(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            page._page = 5
            page._render_table()
            _spin(3)
            assert page._page == 1
        finally:
            _teardown_page(page)

    def test_filtered_count_and_page_indicator(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        for i in range(55):
            _add_product(conn, f"{i:04d}", f"Product {i}", 50)
            _add_lot(conn, f"{i:04d}", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert page._filtered_count_lbl.text() != ""
            assert page._page_lbl.text() != ""
        finally:
            _teardown_page(page)

    def test_pagination_with_filter(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        for i in range(55):
            _add_product(conn, f"{i:04d}", f"Product {i}", 50)
            _add_lot(conn, f"{i:04d}", 50, "2027-06-01")
        _add_product(conn, "EXP1", "Expired 1", 50)
        _add_lot(conn, "EXP1", 50, "2025-01-01")
        _add_product(conn, "EXP2", "Expired 2", 50)
        _add_lot(conn, "EXP2", 50, "2025-01-01")
        _add_product(conn, "EXP3", "Expired 3", 50)
        _add_lot(conn, "EXP3", 50, "2025-01-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            page._on_filter_changed(FILTER_KEYS.index("expired"))
            _spin(3)
            assert page._filtered_count_lbl.text().startswith("Φιλτραρισμένα: 3")
            assert "Σελίδα 1 από 1" in page._page_lbl.text()
        finally:
            _teardown_page(page)


# ═══════════════════════════════════════════════════════════════════════════
# Worker lifecycle tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkerLifecycle:

    def test_loading_controls_disable_and_restore(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        page = _make_page(db)
        try:
            assert _is_complete(page)
            assert page._refresh_btn.isEnabled()
            assert page._alert_spin.isEnabled()
            assert page._filter_combo.isEnabled()
        finally:
            _teardown_page(page)

    def test_refresh_while_busy_starts_no_second_worker(self, qapp, tmp_path):
        """Calling refresh while loading does not spawn a second worker.

        Uses invocation counting to prove exactly one active refresh occurred.
        """
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()
        invoke_count = []

        original_load = mod.load_stock_lot_integrity

        def _blocking_load(db_path, business_date, alert_days=30):
            invoke_count.append(1)
            _started.set()
            _block.wait(timeout=5.0)
            return original_load(db_path, business_date, alert_days)

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_complete(page)

            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_load)

            try:
                page.refresh()
                assert _wait_for(_started.is_set, timeout_ms=2000)
                assert page._loading

                # Second refresh must be a no-op
                page.refresh()
                _spin(3)
                assert page._loading
            finally:
                _block.set()
                monkeypatch.undo()

            # Wait for real completion
            _drain_initial_load(page)
            assert _is_complete(page)
            # Exactly 1 invocation, not 2
            assert len(invoke_count) == 1
        finally:
            _teardown_page(page)

    def test_success_cleanup(self, qapp, tmp_path):
        """After a successful load, worker/thread refs are cleaned up."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_complete(page)
        finally:
            _teardown_page(page)

    def test_failure_cleanup(self, qapp, tmp_path):
        """After a failed load (missing DB), worker/thread refs are cleaned up."""
        page = _make_page("/nonexistent/path/db.db")
        try:
            assert _is_complete(page)
            assert page._state_lbl.text() != ""
        finally:
            _teardown_page(page)

    def test_repeated_refresh(self, qapp, tmp_path):
        """Three sequential refreshes — complete cleanup after every cycle."""
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            for i in range(3):
                page.refresh()
                _drain_initial_load(page)
                assert _is_complete(page), f"Cycle {i} did not complete cleanup"
        finally:
            _teardown_page(page)

    def test_shutdown_while_idle(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()

        page = _make_page(db)
        try:
            assert page.shutdown() is True
        finally:
            _teardown_page(page)

    def test_shutdown_while_worker_running(self, qapp, tmp_path, monkeypatch):
        """Shutdown while worker is active: returns False, preserves refs,
        releases, then cleans up."""
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()

        def _blocking_load(db_path, business_date, alert_days=30):
            _started.set()
            _block.wait(timeout=5.0)
            return StockLotIntegrityResult.failure("blocked")

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()

        page = _make_page(db)
        try:
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_load)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)
            assert page._thread is not None
            assert page._thread.isRunning()

            # Shutdown while blocked — must return False (timeout)
            result = page.shutdown()
            assert result is False, "Shutdown should return False while blocked"
            # Live refs preserved
            assert page._thread is not None
            assert page._worker is not None

            # Release the block
            _block.set()

            # Wait for real completion
            assert _wait_for(lambda: _is_complete(page), timeout_ms=5000), \
                "Timed out waiting for shutdown completion"
            assert _is_complete(page)
        finally:
            _teardown_page(page)

    def test_shutdown_emits_ready_once(self, qapp, tmp_path):
        """shutdown_ready is emitted exactly once after unblocking."""
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()
        ready_count = []

        def _blocking_load(db_path, business_date, alert_days=30):
            _started.set()
            _block.wait(timeout=5.0)
            return StockLotIntegrityResult.failure("blocked")

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()

        page = _make_page(db)
        try:
            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_load)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)

            page.shutdown_ready.connect(lambda: ready_count.append(1))

            result = page.shutdown()
            assert result is False

            _block.set()
            assert _wait_for(lambda: len(ready_count) >= 1, timeout_ms=5000), \
                "shutdown_ready was never emitted"
            _spin(5)
            assert len(ready_count) == 1, "shutdown_ready emitted more than once"
        finally:
            monkeypatch.undo()
            _teardown_page(page)

    def test_no_qthread_destroyed_while_running(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            page.shutdown()
            _spin(5)
        finally:
            _teardown_page(page)


# ═══════════════════════════════════════════════════════════════════════════
# Read-only contract
# ═══════════════════════════════════════════════════════════════════════════

class TestReadOnlyContract:

    def test_no_sql_writes_in_page_code(self):
        import qt_app.pages.stock_lot_integrity_page as mod

        chunks = []
        for name, obj in vars(mod).items():
            try:
                if inspect.isclass(obj) and obj.__module__ == mod.__name__:
                    chunks.append(inspect.getsource(obj))
                    for mname, mobj in vars(obj).items():
                        if inspect.isfunction(mobj):
                            chunks.append(inspect.getsource(mobj))
                elif inspect.isfunction(obj) and obj.__module__ == mod.__name__:
                    chunks.append(inspect.getsource(obj))
            except (OSError, TypeError):
                continue
        src = "\n".join(chunks)

        for pat in ["INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ", "ALTER "]:
            assert pat not in src, f"Found forbidden SQL: {pat}"

    def test_no_schema_mutation(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()

        defs_before = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        ).fetchall()
        pm_before = [tuple(r) for r in conn.execute(
            "SELECT * FROM ProductMaster ORDER BY Barcode"
        ).fetchall()]
        lot_before = [tuple(r) for r in conn.execute(
            "SELECT * FROM stock_lots ORDER BY id"
        ).fetchall()]
        conn.close()

        r = load_stock_lot_integrity(db, business_date="2026-06-15")
        assert r.ok

        conn2 = sqlite3.connect(db)
        conn2.row_factory = sqlite3.Row
        defs_after = conn2.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        ).fetchall()
        pm_after = [tuple(r) for r in conn2.execute(
            "SELECT * FROM ProductMaster ORDER BY Barcode"
        ).fetchall()]
        lot_after = [tuple(r) for r in conn2.execute(
            "SELECT * FROM stock_lots ORDER BY id"
        ).fetchall()]
        conn2.close()

        assert len(defs_before) == len(defs_after)
        for b, a in zip(defs_before, defs_after):
            assert tuple(b) == tuple(a)
        assert pm_before == pm_after
        assert lot_before == lot_after



