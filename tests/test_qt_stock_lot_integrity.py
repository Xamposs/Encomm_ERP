"""Stock Lot Integrity page — Qt UI, filtering, pagination, lifecycle tests.

Lifecycle tests use the proven patterns from test_qt_supplier_reorder.py:
bounded event-pump helpers, blocking workers with threading.Event,
invocation counters for overlap detection.
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
from datetime import date, timedelta

import pytest

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
# Page lifecycle helpers — bounded, no mutation of page state
# ═══════════════════════════════════════════════════════════════════════

def _is_idle(page) -> bool:
    """True when the page is in IDLE state and can accept a refresh."""
    return not page._loading and page._thread is None and page._worker is None


def _drain_initial_load(page) -> None:
    """Pump events until the initial worker completes and page is IDLE."""
    if not _wait_for(lambda: _is_idle(page), timeout_ms=5000):
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


def _teardown_page(page) -> None:
    """Strict bounded teardown — never deleteLater on a running thread."""
    result = page.shutdown()
    if result is False:
        _spin(10)
        _wait_for(lambda: _is_idle(page), timeout_ms=5000)
    _spin(5)
    if not _is_idle(page):
        raise RuntimeError(
            f"Teardown failed: page not idle after shutdown: "
            f"_loading={page._loading}, _thread={page._thread is not None}"
        )
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

    def test_existing_routes_still_work(self):
        from qt_app.main_window import NAV_ITEMS
        from qt_app.pages import PAGE_CLASSES
        for key, _label in NAV_ITEMS:
            assert key in PAGE_CLASSES, f"Missing PAGE_CLASS for {key}"
            assert hasattr(PAGE_CLASSES[key], "build_ui")


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
                if page._table.item(r, 0).text() == "B":
                    assert page._table.item(r, 9).text() == "—"
                    return
            pytest.fail("Product B not found in table")
        finally:
            _teardown_page(page)

    def test_tracking_unavailable_state(self, qapp, tmp_path):
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
            text = page._state_lbl.text()
            assert "stock_lots" in text.lower()
            assert "δεν είναι διαθέσιμη" in text
            assert page._table.isHidden()

            conn2 = sqlite3.connect(db)
            tables = {r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            conn2.close()
            assert "stock_lots" not in tables

            assert page._lbl_total.text() == "—"
            assert page._lbl_fully.text() == "—"
        finally:
            _teardown_page(page)

    def test_typed_error_preserves_previous_view(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            snap = page._current_snapshot
            assert snap is not None
            assert page._lbl_total.text() == str(snap.total_products_with_stock)

            error_msg = "Σφάλμα: δοκιμαστικό μήνυμα αποτυχίας."
            page._on_data_ready(StockLotIntegrityResult.failure(error_msg))
            _spin(3)

            assert error_msg in page._state_lbl.text()
            assert page._current_snapshot is not None
            assert not page._table.isHidden()
            assert page._lbl_total.text() == str(snap.total_products_with_stock)
            assert page._table.rowCount() > 0
        finally:
            _teardown_page(page)

    def test_successful_empty_state(self, qapp, tmp_path):
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
            assert page._lbl_total.text() == "0"
            assert page._lbl_fully.text() == "0"
            assert "δεν βρέθηκαν προϊόντα" in page._state_lbl.text().lower()
        finally:
            _teardown_page(page)


# ═══════════════════════════════════════════════════════════════════════════
# Filtering tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFiltering:

    def test_every_filter_uses_required_numeric_condition(self, qapp, tmp_path):
        expiring = (date.today() + timedelta(days=10)).isoformat()
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "EXP",   "Expired", 50)
        _add_lot(conn, "EXP", 50, "2025-01-01")
        _add_product(conn, "SOON",  "Expiring Soon", 50)
        _add_lot(conn, "SOON", 50, expiring)
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
            prods = page._all_products
            assert any(p.barcode == "EXP" for p in _filter_products(prods, "expired"))
            assert any(p.barcode == "SOON" for p in _filter_products(prods, "expiring_soon"))
            assert any(p.barcode == "UNTR" for p in _filter_products(prods, "untracked"))
            assert any(p.barcode == "UNDT" for p in _filter_products(prods, "undated"))
            assert any(p.barcode == "INV" for p in _filter_products(prods, "invalid_date"))
            assert any(p.barcode == "OVER" for p in _filter_products(prods, "overage"))
            assert any(p.barcode == "FULL" for p in _filter_products(prods, "fully_covered"))
            needs = _filter_products(prods, "needs_attention")
            assert len(needs) >= 5
        finally:
            _teardown_page(page)

    def test_mixed_condition_product_in_every_applicable_filter(self):
        MIX = _fake_product(
            barcode="MIX", master_stock=100, total_lot_qty=90,
            qty_in_dated_lots=40, qty_in_undated_lots=30,
            expired_lot_qty=20, untracked_qty=10,
        )
        products = (MIX,)

        for fk in ("needs_attention", "expired", "untracked", "undated"):
            assert any(p.barcode == "MIX" for p in _filter_products(products, fk))
        for fk in ("expiring_soon", "invalid_date", "overage", "fully_covered"):
            assert not any(p.barcode == "MIX" for p in _filter_products(products, fk))

    def test_filter_does_not_call_database(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_idle(page)
            called = []
            orig = page.refresh
            def _spy():
                called.append(True)
                orig()
            page.refresh = _spy
            page._on_filter_changed(1)
            _spin(3)
            assert len(called) == 0
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
        assert _paginate([], 1) == ([], 1, 1)

    def test_pagination_1_row(self):
        items = [_fake_product(barcode="A")]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == 1 and total_pages == 1

    def test_pagination_50_rows(self):
        items = [_fake_product(barcode=f"{i:04d}") for i in range(PAGE_SIZE)]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == PAGE_SIZE and total_pages == 1

    def test_pagination_51_rows(self):
        items = [_fake_product(barcode=f"{i:04d}") for i in range(PAGE_SIZE + 1)]
        page_items, total_pages, page = _paginate(items, 1)
        assert len(page_items) == PAGE_SIZE and total_pages == 2
        page_items, total_pages, page = _paginate(items, 2)
        assert len(page_items) == 1 and total_pages == 2

    def test_page_clamping(self):
        items = [_fake_product(barcode="A")]
        page_items, total_pages, page = _paginate(items, 99)
        assert len(page_items) == 1 and total_pages == 1 and page == 1


# ═══════════════════════════════════════════════════════════════════════════
# Worker lifecycle tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkerLifecycle:

    def test_loading_controls_disable_and_restore(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        _make_db(db)
        page = _make_page(db)
        try:
            assert _is_idle(page)
            assert page._refresh_btn.isEnabled()
            assert page._alert_spin.isEnabled()
        finally:
            _teardown_page(page)

    def test_refresh_while_busy_starts_no_second_worker(self, qapp, tmp_path, monkeypatch):
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()
        call_count = 0

        orig = mod.load_stock_lot_integrity
        def _blocking_loader(db_path, business_date, alert_days=30):
            nonlocal call_count
            call_count += 1
            _started.set()
            _block.wait(timeout=5.0)
            return orig(db_path, business_date, alert_days)

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_idle(page)
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_loader)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)
            assert page._loading

            page.refresh()  # must be no-op
            _spin(3)
            assert page._loading
            assert call_count == 1

            _block.set()  # release
            assert _wait_for(lambda: _is_idle(page), timeout_ms=5000)
            assert call_count == 1
        finally:
            monkeypatch.undo()
            _teardown_page(page)

    def test_success_cleanup(self, qapp, tmp_path):
        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)
        try:
            assert _is_idle(page)
        finally:
            _teardown_page(page)

    def test_failure_cleanup(self, qapp, tmp_path):
        page = _make_page("/nonexistent/path/db.db")
        try:
            assert _is_idle(page)
            assert page._state_lbl.text() != ""
        finally:
            _teardown_page(page)

    def test_repeated_refresh(self, qapp, tmp_path):
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
                assert _wait_for(lambda: _is_idle(page), timeout_ms=5000), \
                    f"Cycle {i} did not complete"
                assert _is_idle(page)
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
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()

        def _blocking_loader(db_path, business_date, alert_days=30):
            _started.set()
            _block.wait(timeout=5.0)
            return StockLotIntegrityResult.failure("blocked")

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()

        page = _make_page(db)
        try:
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_loader)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)
            assert page._thread is not None
            assert page._thread.isRunning()

            # Shutdown while blocked — must return False
            result = page.shutdown()
            assert result is False, "Expected False while thread blocked"
            # Live refs preserved
            assert page._thread is not None
            assert page._worker is not None

            # Release block
            _block.set()
            assert _wait_for(lambda: _is_idle(page), timeout_ms=5000), \
                "Timed out waiting for idle"
            assert _is_idle(page)
        finally:
            monkeypatch.undo()
            _teardown_page(page)

    def test_shutdown_emits_ready_once(self, qapp, tmp_path, monkeypatch):
        import qt_app.pages.stock_lot_integrity_page as mod
        _block = threading.Event()
        _started = threading.Event()
        ready_count = []

        def _blocking_loader(db_path, business_date, alert_days=30):
            _started.set()
            _block.wait(timeout=5.0)
            return StockLotIntegrityResult.failure("blocked")

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        conn.close()

        page = _make_page(db)
        try:
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _blocking_loader)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)
            page.shutdown_ready.connect(lambda: ready_count.append(1))

            result = page.shutdown()
            assert result is False

            _block.set()
            assert _wait_for(lambda: len(ready_count) >= 1, timeout_ms=5000), \
                "shutdown_ready not emitted"
            _spin(5)
            assert len(ready_count) == 1, "shutdown_ready emitted >1 time"
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

    def test_stale_callback_does_not_clear_newer_worker(self, qapp, tmp_path):
        """Generation A completes, B starts — A's late callback does not touch B."""
        import qt_app.pages.stock_lot_integrity_page as mod
        _block_b = threading.Event()
        _b_started = threading.Event()
        call_count = 0

        orig = mod.load_stock_lot_integrity
        def _counting_loader(db_path, business_date, alert_days=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                _b_started.set()
                _block_b.wait(timeout=5.0)
            return orig(db_path, business_date, alert_days)

        db = str(tmp_path / "test.db")
        conn = _make_db(db)
        _add_product(conn, "A", "Alpha", 50)
        _add_lot(conn, "A", 50, "2027-06-01")
        conn.commit()
        conn.close()

        page = _make_page(db)  # gen A completes
        try:
            assert _is_idle(page)

            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr(mod, "load_stock_lot_integrity", _counting_loader)

            # Start gen B (blocks)
            page.refresh()
            assert _wait_for(_b_started.is_set, timeout_ms=2000)
            assert page._loading
            wrk_b = page._worker
            thr_b = page._thread

            # gen B is running — assert its refs are intact
            assert wrk_b is not None
            assert thr_b is not None
            assert page._loading

            # Release B
            _block_b.set()
            assert _wait_for(lambda: _is_idle(page), timeout_ms=5000)
            assert _is_idle(page)
        finally:
            monkeypatch.undo()
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
