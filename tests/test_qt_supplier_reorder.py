"""Supplier Reorder Candidates page — Qt UI and lifecycle tests (P3.2)."""

from __future__ import annotations

import inspect
import sqlite3
import threading

import pytest

# Qt offscreen — conftest handles this, but keep import-safe
pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QGroupBox, QTableWidget, QSpinBox, QPushButton,
    QLabel, QScrollArea, QMessageBox,
)


# ── Safe event pumping (scoped, per-window, like test_qt_desktop_layout) ──

def _spin(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Block real modal QMessageBoxes in offscreen tests."""
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_reorder_db(
    path: str,
    products: list[tuple],
    suppliers: list[tuple] | None = None,
) -> None:
    """Create a temp DB with ProductMaster and optional suppliers."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE ProductMaster (
            Barcode      TEXT PRIMARY KEY,
            Name         TEXT NOT NULL,
            Stock        INTEGER NOT NULL,
            ExpiryDate   TEXT NOT NULL,
            Price        REAL NOT NULL,
            supplier_id  INTEGER
        )
    """)
    if suppliers is not None:
        conn.execute("""
            CREATE TABLE suppliers (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
        for sup in suppliers:
            conn.execute(
                "INSERT INTO suppliers (id, name) VALUES (?, ?)", sup)
    for p in products:
        conn.execute(
            "INSERT INTO ProductMaster VALUES (?,?,?,?,?,?)", p)
    conn.commit()
    conn.close()


def _make_page(db_path: str, threshold: int = 10):
    """Create a SupplierReorderPage, wait for initial worker, return it."""
    from qt_app.pages.supplier_reorder_page import SupplierReorderPage
    config = {"db_path": db_path}
    page = SupplierReorderPage(db_service=None, config=config)
    page._threshold = threshold
    page._threshold_spin.setValue(threshold)
    # Wait for the initial refresh worker and drain queued signals.
    if page._thread is not None:
        page._thread.wait(5000)
    # Drain the worker.finished → _on_data_ready + thread.quit signals
    _spin(8)
    # Fire the thread-done handler (which also drains thread.finished signals)
    page._on_thread_done()
    return page


def _teardown_page(page) -> None:
    """Shut down the page and clean up its Qt resources."""
    try:
        page.shutdown()
    except Exception:
        pass
    _spin(3)
    page.deleteLater()
    _spin(3)


# ── Page structure tests ────────────────────────────────────────────────

class TestSupplierReorderPageStructure:
    """Verify the page is built with the expected widgets."""

    def test_page_has_threshold_spinbox(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            spins = page.findChildren(QSpinBox)
            assert len(spins) >= 1
            spin = spins[0]
            assert spin.minimum() == 1
            assert spin.maximum() == 10000
        finally:
            _teardown_page(page)

    def test_page_has_refresh_button(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            btns = page.findChildren(QPushButton)
            refresh_btns = [
                b for b in btns if "Ανανέωση" in (b.text() or "")]
            assert len(refresh_btns) >= 1
        finally:
            _teardown_page(page)

    def test_page_has_summary_and_state_labels(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            labels = page.findChildren(QLabel)
            assert len(labels) >= 2  # summary + state_lbl at minimum
        finally:
            _teardown_page(page)

    def test_page_has_scroll_area(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            scrolls = page.findChildren(QScrollArea)
            assert len(scrolls) >= 1
        finally:
            _teardown_page(page)

    def test_page_registered_in_page_classes(self):
        """supplier_reorder is in PAGE_CLASSES."""
        from qt_app.pages import PAGE_CLASSES
        assert "supplier_reorder" in PAGE_CLASSES
        from qt_app.pages.supplier_reorder_page import SupplierReorderPage
        assert PAGE_CLASSES["supplier_reorder"] is SupplierReorderPage

    def test_page_in_nav_and_titles(self):
        """supplier_reorder is in NAV_ITEMS and PAGE_TITLES."""
        from qt_app.main_window import NAV_ITEMS, PAGE_TITLES
        keys = [k for k, _ in NAV_ITEMS]
        assert "supplier_reorder" in keys
        assert "supplier_reorder" in PAGE_TITLES
        assert PAGE_TITLES["supplier_reorder"] == "Υποψήφιοι Αναπαραγγελίας"


# ── Lifecycle tests ─────────────────────────────────────────────────────

class TestSupplierReorderPageLifecycle:
    """Thread/worker lifecycle: loading flag, shutdown, close-pending."""

    def test_refresh_sets_loading(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [("A", "Alpha", 3, "2027-01-01", 10.0, 1)],
                         suppliers=[(1, "S1")])
        page = _make_page(db)
        try:
            assert not page._loading

            page.refresh()
            assert page._loading
            assert not page._refresh_btn.isEnabled()

            _spin(8)
        finally:
            _teardown_page(page)

    def test_on_thread_done_clears_loading(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            assert not page._loading
            assert page._refresh_btn.isEnabled()
            assert page._worker is None
            assert page._thread is None
        finally:
            _teardown_page(page)

    def test_shutdown_returns_true_when_idle(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            result = page.shutdown()
            assert result is True
        finally:
            _teardown_page(page)

    def test_shutdown_while_worker_running(self, qapp, tmp_path, monkeypatch):
        """shutdown() returns False when worker is blocked."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [("A", "X", 1, "2027-01-01", 1.0, 1)],
                         suppliers=[(1, "S1")])

        import qt_app.pages.supplier_reorder_page as srp_mod

        _block = threading.Event()
        _started = threading.Event()

        def _blocking_load(db_path, threshold):
            _started.set()
            _block.wait(timeout=5.0)
            from qt_app.data_source import SupplierReorderResult
            return SupplierReorderResult.success((), ())

        monkeypatch.setattr(srp_mod, "load_supplier_reorder_candidates",
                            _blocking_load)

        page = _make_page(db)
        try:
            assert not page._loading

            page.refresh()
            _started.wait(timeout=2.0)
            assert page._thread is not None
            assert page._thread.isRunning()

            result = page.shutdown()
            assert result is False
            assert page._thread is not None

            _block.set()
            _spin(10)
        finally:
            _teardown_page(page)

    def test_on_data_ready_discards_when_close_pending(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            page._close_pending = True
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            fake = SupplierReorderResult.success(
                (SupplierReorderGroup(1, "S1", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                )),),
                (),
            )
            page._on_data_ready(fake)
            assert page._close_pending
        finally:
            _teardown_page(page)

    def test_error_state_on_failure(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import SupplierReorderResult
            page._on_data_ready(
                SupplierReorderResult.failure("Δοκιμαστικό σφάλμα"))
            assert "Δοκιμαστικό σφάλμα" in page._state_lbl.text()
            assert page._summary.text() == ""
        finally:
            _teardown_page(page)

    def test_empty_result_shows_empty_message(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import SupplierReorderResult
            page._on_data_ready(SupplierReorderResult.success((), ()))
            assert "Δεν βρέθηκαν" in page._state_lbl.text()
        finally:
            _teardown_page(page)


# ── Data rendering tests ────────────────────────────────────────────────

class TestSupplierReorderPageRendering:
    """Verify grouped and unassigned products appear in the UI."""

    def test_grouped_products_rendered_as_group_boxes(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            fake = SupplierReorderResult.success(
                (SupplierReorderGroup(1, "Φάρμακο ΑΕ", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                    ReorderCandidate("B", "Beta", 5, 10, "2027-08-01", 20.0),
                )),),
                (),
            )
            page._on_data_ready(fake)
            _spin(3)

            boxes = page._scroll_widget.findChildren(QGroupBox)
            assert len(boxes) == 1
            assert "Φάρμακο ΑΕ" in boxes[0].title()

            tables = boxes[0].findChildren(QTableWidget)
            assert len(tables) == 1
            table = tables[0]
            assert table.rowCount() == 2
            assert table.item(0, 0).text() == "A"
            assert table.item(0, 1).text() == "Alpha"
            assert table.item(1, 0).text() == "B"
        finally:
            _teardown_page(page)

    def test_unassigned_products_rendered_separately(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, UnassignedReorderProduct,
            )
            fake = SupplierReorderResult.success(
                (),
                (UnassignedReorderProduct(
                    "C", "Gamma", 2, 10, "2027-03-01", 15.0,
                    reason="Χωρίς προμηθευτή",
                ),),
            )
            page._on_data_ready(fake)
            _spin(3)

            boxes = page._scroll_widget.findChildren(QGroupBox)
            assert len(boxes) == 1
            assert "Αταξινόμητα" in boxes[0].title()

            tables = boxes[0].findChildren(QTableWidget)
            assert len(tables) == 1
            table = tables[0]
            assert table.rowCount() == 1
            assert table.item(0, 0).text() == "C"
            assert table.item(0, 1).text() == "Gamma"
            assert table.item(0, 6).text() == "Χωρίς προμηθευτή"
        finally:
            _teardown_page(page)

    def test_mixed_groups_and_unassigned(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup,
                UnassignedReorderProduct, ReorderCandidate,
            )
            fake = SupplierReorderResult.success(
                (SupplierReorderGroup(1, "MediCorp", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                )),),
                (UnassignedReorderProduct(
                    "B", "Beta", 5, 10, "2027-01-01", 20.0,
                    reason="Ο προμηθευτής δεν υπάρχει",
                ),),
            )
            page._on_data_ready(fake)
            _spin(3)

            boxes = page._scroll_widget.findChildren(QGroupBox)
            assert len(boxes) == 2
            titles = [b.title() for b in boxes]
            assert any("MediCorp" in t for t in titles)
            assert any("Αταξινόμητα" in t for t in titles)
        finally:
            _teardown_page(page)

    def test_summary_shows_correct_counts(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            fake = SupplierReorderResult.success(
                (SupplierReorderGroup(1, "S1", (
                    ReorderCandidate("A", "Alpha", 5, 10, "2027-01-01", 10.0),
                )),),
                (),
            )
            page._on_data_ready(fake)
            assert "1 προϊόντα" in page._summary.text()
            assert "1 προμηθευτές" in page._summary.text()
        finally:
            _teardown_page(page)

    def test_refresh_clears_previous_content(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            # Feed grouped data
            page._on_data_ready(SupplierReorderResult.success(
                (SupplierReorderGroup(1, "S1", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                )),),
                (),
            ))
            _spin(6)
            assert len(page._scroll_widget.findChildren(QGroupBox)) == 1

            # Feed empty data — content must be cleared, empty message shown
            page._on_data_ready(SupplierReorderResult.success((), ()))
            _spin(6)
            # After empty result, state label shows empty message
            assert "Δεν βρέθηκαν" in page._state_lbl.text()
            # The grouped table should be hidden via state label
            assert not page._scroll.isVisible() or page._state_lbl.isVisible()
        finally:
            _teardown_page(page)

    def test_no_write_in_page_source(self):
        """SupplierReorderPage source contains no SQL or write operations."""
        from qt_app.pages import supplier_reorder_page as srp
        src = inspect.getsource(srp.SupplierReorderPage)
        forbidden = ["sqlite3", "INSERT", "UPDATE", "DELETE", "DROP",
                     "CREATE TABLE", "ALTER", "execute(", "executemany("]
        for pat in forbidden:
            assert pat not in src, (
                f"SupplierReorderPage source must not contain '{pat}'")


# ── Navigation integration test ─────────────────────────────────────────

class TestNavigationIntegration:
    """Verify the new page is reachable via MainWindow navigation."""

    def test_navigate_to_supplier_reorder(self, qapp, tmp_path):
        from qt_app.main_window import MainWindow
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        config = {"db_path": db}

        window = MainWindow(db_service=None, config=config)
        try:
            window.navigate_to("supplier_reorder")
            _spin(5)
            page = window._pages.get("supplier_reorder")
            assert page is not None
            from qt_app.pages.supplier_reorder_page import SupplierReorderPage
            assert isinstance(page, SupplierReorderPage)
        finally:
            for p in list(getattr(window, "_pages", {}).values()):
                if hasattr(p, "shutdown"):
                    try:
                        p.shutdown()
                    except Exception:
                        pass
            _spin(3)
            window.close()
            window.deleteLater()
            _spin(3)
            QCoreApplication.sendPostedEvents(
                window, QEvent.DeferredDelete)
            _spin(2)
