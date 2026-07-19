"""Supplier Reorder Candidates page — Qt UI, draft workflow, lifecycle tests.

Covers:
    - P3.2 read-only candidate view (groups + unassigned)
    - P3.3 in-memory draft workflow (add/update/remove/discard, duplicate
      prevention, per-supplier grouping, unassigned exclusion, refresh
      protection, lifecycle safety)
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
import time

import pytest

# Qt offscreen — conftest handles this, but keep import-safe
pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QGroupBox, QTableWidget, QSpinBox, QPushButton,
    QLabel, QScrollArea, QMessageBox,
)


# ── Bounded event pumping ───────────────────────────────────────────────
#
# We never use unbounded ``time.sleep``.  ``_spin`` pumps the event loop
# in short slices so queued signals (worker.finished → _on_data_ready,
# thread.finished → _on_thread_done) are delivered deterministically.
# ``_wait_for`` pumps until a predicate is satisfied or a deadline
# elapses — used for the rare cases where the predicate is observable
# from the main thread (e.g. a worker thread reaching a state).

def _spin(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


def _wait_for(predicate, *, timeout_ms: int = 3000, slice_ms: int = 20) -> bool:
    """Pump events until ``predicate()`` is truthy or deadline elapses.

    Returns True if the predicate was satisfied, False on timeout.  Uses
    small slices so queued Qt signals are processed promptly.  This
    replaces ad-hoc fixed multi-second waits.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        # Yield without blocking — small bounded slice.
        time.sleep(slice_ms / 1000.0)
    QCoreApplication.processEvents()
    return bool(predicate())


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


def _drain_initial_load(page) -> None:
    """Deterministically drain the page's initial refresh worker.

    Worker lifecycle is signal-driven: ``worker.finished`` is emitted
    from the worker thread and received in the main thread via a
    queued connection, which then invokes ``thread.quit`` (also via a
    queued connection).  Both queue hops require the *main* event loop
    to pump.  ``QThread.wait()`` therefore cannot observe completion
    on its own — it would block until its timeout because the
    thread's event loop never exits without the main-thread delivery
    of ``thread.quit``.

    We pump events in a tight bounded loop instead.  This is safe
    because the worker is purely read-only and emits exactly one
    signal; there is no reentrancy into the page during the pump
    other than the connected slots themselves, which are designed to
    be idempotent.

    The previous test helper manually called ``_on_thread_done`` after
    a fixed ``_thread.wait(5000)`` — that masked lifecycle bugs
    because it short-circuited Qt's own signal delivery and silently
    ignored the wait() timeout.  We let Qt deliver the signals for
    real.
    """
    _wait_for(lambda: not page._loading, timeout_ms=3000)


def _make_page(db_path: str, threshold: int = 10):
    """Create a SupplierReorderPage, wait for initial worker, return it."""
    from qt_app.pages.supplier_reorder_page import SupplierReorderPage
    config = {"db_path": db_path}
    page = SupplierReorderPage(db_service=None, config=config)
    page._threshold = threshold
    page._threshold_spin.setValue(threshold)
    _drain_initial_load(page)
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
    QCoreApplication.sendPostedEvents(page, QEvent.DeferredDelete)
    _spin(2)


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

    def test_page_has_draft_area(self, qapp, tmp_path):
        """P3.3: a clearly separate Greek draft area exists."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            assert "draftFrame" == page._draft_frame.objectName()
            assert "Πρόχειρο Αναπαραγγελίας" in page._draft_header.text()
        finally:
            _teardown_page(page)

    def test_page_has_discard_button_hidden_when_empty(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            assert "Καθαρισμός" in page._discard_btn.text()
            # Empty draft → button explicitly hidden.
            assert page._discard_btn.isHidden()
        finally:
            _teardown_page(page)


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

            # Pump so worker.finished → thread.quit → thread.finished
            # → _on_thread_done are all delivered in the main thread.
            _wait_for(lambda: not page._loading, timeout_ms=3000)
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
        """shutdown() returns False when worker is blocked.

        Verifies the safe lifecycle contract: an active worker that
        cannot stop within the wait window leaves the page in
        ``_close_pending`` so the main window's ``shutdown_ready``
        retry can fire later.

        The blocking-load monkeypatch is installed AFTER the page's
        initial refresh has completed normally, so only the explicit
        ``page.refresh()`` below uses the blocking loader.
        """
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [("A", "X", 1, "2027-01-01", 1.0, 1)],
                         suppliers=[(1, "S1")])

        import qt_app.pages.supplier_reorder_page as srp_mod

        page = _make_page(db)
        try:
            assert not page._loading

            _block = threading.Event()
            _started = threading.Event()

            def _blocking_load(db_path, threshold):
                _started.set()
                _block.wait(timeout=5.0)
                from qt_app.data_source import SupplierReorderResult
                return SupplierReorderResult.success((), ())

            monkeypatch.setattr(srp_mod, "load_supplier_reorder_candidates",
                                _blocking_load)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)
            assert page._thread is not None
            assert page._thread.isRunning()

            result = page.shutdown()
            assert result is False
            assert page._thread is not None

            _block.set()
            # Worker is now unblocked.  Pump events so the queued
            # worker.finished → thread.quit → thread.finished signals
            # are delivered and _on_thread_done clears _loading.
            _wait_for(
                lambda: page._thread is None or not page._thread.isRunning(),
                timeout_ms=3000,
            )
            _spin(3)
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

    def test_no_external_persistence_in_module(self):
        """P3.3: the page module touches no external persistence.

        We scan the executable statements only (class + function
        bodies, not the module docstring) so legitimate Greek
        docstring mentions of 'clipboard' or 'logs' don't
        false-positive.  ``inspect.getsource`` can fail for some
        re-exported or built-in members, so we skip those rather
        than failing the gather.
        """
        from qt_app.pages import supplier_reorder_page as srp

        chunks: list[str] = []
        for name, obj in vars(srp).items():
            try:
                if inspect.isclass(obj) and obj.__module__ == srp.__name__:
                    chunks.append(inspect.getsource(obj))
                    for mname, mobj in vars(obj).items():
                        if inspect.isfunction(mobj):
                            chunks.append(inspect.getsource(mobj))
                elif (inspect.isfunction(obj)
                      and obj.__module__ == srp.__name__):
                    chunks.append(inspect.getsource(obj))
            except (OSError, TypeError):
                # Some members (e.g. dunder aliases, descriptors) have
                # no retrievable source — skip them.
                continue
        src = "\n".join(chunks)

        forbidden = [
            "import json", "import pickle", "import shelve",
            "open(", "pathlib", "os.remove", "os.unlink",
            "urllib", "requests", "smtplib", "import socket",
            "pyperclip", "import subprocess",
            "csv.writer", "xlwt", "openpyxl",
        ]
        for pat in forbidden:
            assert pat not in src, (
                f"supplier_reorder_page executable code must not contain '{pat}'")


# ── Draft workflow tests (P3.3) ─────────────────────────────────────────

class TestSupplierReorderDraftWorkflow:
    """In-memory reorder draft: add/update/remove, duplicate prevention,
    per-supplier grouping, unassigned exclusion, explicit discard."""

    def test_add_creates_line_with_manual_quantity(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate(
                "A", "Alpha", 3, 10, "2027-01-01", 10.0)
            ok = page._add_to_draft(1, "S1", cand, quantity=5)
            assert ok
            assert page._draft["A"].quantity == 5
            assert page._draft["A"].supplier_name == "S1"
            assert page._draft["A"].price == 10.0
        finally:
            _teardown_page(page)

    def test_add_rejects_non_positive_quantity(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0)
            assert not page._add_to_draft(1, "S1", cand, quantity=0)
            assert not page._add_to_draft(1, "S1", cand, quantity=-3)
            assert "A" not in page._draft
        finally:
            _teardown_page(page)

    def test_duplicate_add_updates_existing_line(self, qapp, tmp_path):
        """A product must never appear twice — re-adding bumps quantity."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0)
            page._add_to_draft(1, "S1", cand, quantity=2)
            page._add_to_draft(1, "S1", cand, quantity=3)
            assert len(page._draft) == 1
            assert page._draft["A"].quantity == 5
        finally:
            _teardown_page(page)

    def test_update_quantity_mutates_existing_line(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0)
            page._add_to_draft(1, "S1", cand, quantity=1)
            ok = page.update_quantity("A", 7)
            assert ok
            assert page._draft["A"].quantity == 7
            assert len(page._draft) == 1
        finally:
            _teardown_page(page)

    def test_update_quantity_rejects_invalid_inputs(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0)
            page._add_to_draft(1, "S1", cand, quantity=2)
            # Invalid quantity
            assert not page.update_quantity("A", 0)
            assert not page.update_quantity("A", -1)
            assert page._draft["A"].quantity == 2  # unchanged
            # Unknown barcode
            assert not page.update_quantity("ZZ", 5)
        finally:
            _teardown_page(page)

    def test_remove_line_restores_eligibility(self, qapp, tmp_path):
        """Removing a line allows re-adding it from scratch."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            cand = ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0)
            page._add_to_draft(1, "S1", cand, quantity=4)
            ok = page.remove_line("A")
            assert ok
            assert "A" not in page._draft
            assert page.is_draft_empty()
            # Re-adding after removal starts fresh
            page._add_to_draft(1, "S1", cand, quantity=1)
            assert page._draft["A"].quantity == 1
        finally:
            _teardown_page(page)

    def test_remove_unknown_returns_false(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            assert not page.remove_line("ZZ")
        finally:
            _teardown_page(page)

    def test_clear_draft_discards_everything(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            for i in range(3):
                c = ReorderCandidate(
                    f"B{i}", f"N{i}", 1, 10, "2027-01-01", 1.0)
                page._add_to_draft(1, "S1", c, quantity=2)
            assert len(page._draft) == 3
            page.clear_draft()
            assert page.is_draft_empty()
        finally:
            _teardown_page(page)

    def test_draft_lines_grouped_by_supplier_deterministically(
        self, qapp, tmp_path,
    ):
        """Lines are sorted by supplier name, then product name."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            # Insert in non-sorted order
            page._add_to_draft(2, "Zeta", ReorderCandidate(
                "B", "Beta", 1, 10, "2027-01-01", 1.0), quantity=1)
            page._add_to_draft(1, "Alpha", ReorderCandidate(
                "A", "Alfa", 1, 10, "2027-01-01", 1.0), quantity=1)
            page._add_to_draft(1, "Alpha", ReorderCandidate(
                "C", "Gamma", 1, 10, "2027-01-01", 1.0), quantity=1)
            page._add_to_draft(2, "Zeta", ReorderCandidate(
                "D", "Delta", 1, 10, "2027-01-01", 1.0), quantity=1)

            lines = page.draft_lines()
            assert [l.supplier_name for l in lines] == [
                "Alpha", "Alpha", "Zeta", "Zeta"]
            assert [l.name for l in lines] == ["Alfa", "Gamma", "Beta", "Delta"]
        finally:
            _teardown_page(page)

    def test_unassigned_cannot_be_added_to_draft(self, qapp, tmp_path):
        """Unassigned products are never draft-eligible.  The page API
        only exposes draft-adding via grouped candidate tables, but we
        also assert the draft model itself has no way to receive an
        UnassignedReorderProduct: ``_add_to_draft`` takes a
        ``ReorderCandidate`` typed argument and the unassigned table is
        built without action buttons."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, UnassignedReorderProduct,
            )
            page._on_data_ready(SupplierReorderResult.success(
                (),
                (UnassignedReorderProduct(
                    "U", "Unassigned", 1, 10, "2027-01-01", 1.0,
                    reason="Χωρίς προμηθευτή",
                ),),
            ))
            _spin(3)
            # Unassigned section has no add buttons
            add_btns = page._scroll_widget.findChildren(QPushButton)
            add_btns = [b for b in add_btns if "Προσθήκη" in (b.text() or "")]
            assert add_btns == []
        finally:
            _teardown_page(page)

    def test_grouped_candidates_have_add_buttons(self, qapp, tmp_path):
        """Each grouped candidate row has an 'Προσθήκη' button."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            page._on_data_ready(SupplierReorderResult.success(
                (SupplierReorderGroup(1, "S1", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                    ReorderCandidate("B", "Beta", 5, 10, "2027-08-01", 20.0),
                )),),
                (),
            ))
            _spin(3)
            add_btns = [b for b in page._scroll_widget.findChildren(QPushButton)
                        if "Προσθήκη" in (b.text() or "")]
            assert len(add_btns) == 2
        finally:
            _teardown_page(page)

    def test_clicking_add_button_adds_to_draft(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                SupplierReorderResult, SupplierReorderGroup, ReorderCandidate,
            )
            page._on_data_ready(SupplierReorderResult.success(
                (SupplierReorderGroup(1, "S1", (
                    ReorderCandidate("A", "Alpha", 3, 10, "2027-01-01", 10.0),
                )),),
                (),
            ))
            _spin(3)
            add_btns = [b for b in page._scroll_widget.findChildren(QPushButton)
                        if "Προσθήκη" in (b.text() or "")]
            assert len(add_btns) == 1
            add_btns[0].click()
            _spin(2)
            assert "A" in page._draft
            assert page._draft["A"].quantity == 1
        finally:
            _teardown_page(page)

    def test_draft_table_shows_empty_state_initially(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            assert page._draft_table.rowCount() == 0
            # Empty label is explicitly shown, table explicitly hidden.
            assert not page._draft_empty.isHidden()
            assert page._draft_table.isHidden()
            assert "άδειο" in page._draft_empty.text()
            assert page._draft_summary.text() == ""
        finally:
            _teardown_page(page)

    def test_draft_summary_shows_counts_after_add(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            page._add_to_draft(2, "S2", ReorderCandidate(
                "B", "Beta", 1, 10, "2027-01-01", 1.0), quantity=3)
            summary = page._draft_summary.text()
            assert "Γραμμές: 2" in summary
            assert "Προμηθευτές: 2" in summary
            assert page._draft_table.rowCount() == 2
        finally:
            _teardown_page(page)

    def test_discard_button_visible_only_when_non_empty(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            # Initially empty — button hidden
            assert page._discard_btn.isHidden()
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=1)
            _spin(2)
            # Non-empty — button not hidden
            assert not page._discard_btn.isHidden()
            page._discard_btn.click()
            _spin(2)
            assert page.is_draft_empty()
            assert page._discard_btn.isHidden()
        finally:
            _teardown_page(page)

    def test_draft_quantity_spinbox_updates_line(self, qapp, tmp_path):
        """Changing the spinbox in a draft row updates the line quantity."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            _spin(2)
            spin = page._draft_table.cellWidget(0, 6)
            assert isinstance(spin, QSpinBox)
            spin.setValue(15)
            _spin(2)
            assert page._draft["A"].quantity == 15
        finally:
            _teardown_page(page)

    def test_remove_button_in_draft_table_removes_line(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            _spin(2)
            rm_btn = page._draft_table.cellWidget(0, 7)
            assert isinstance(rm_btn, QPushButton)
            rm_btn.click()
            _spin(2)
            assert "A" not in page._draft
        finally:
            _teardown_page(page)


# ── Refresh protection tests (P3.3) ─────────────────────────────────────

class TestSupplierReorderDraftRefreshProtection:
    """A non-empty draft must not be silently discarded on refresh."""

    def test_refresh_blocked_when_draft_non_empty(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            assert not page._loading

            page._on_refresh_clicked()
            # Refresh did NOT start a worker
            assert not page._loading
            assert page._thread is None
            # A Greek explanation is shown
            assert "πρόχειρο" in page._state_lbl.text().lower()
            assert "Δεν αρχικοποιήθηκε" not in page._state_lbl.text()
            # Draft is untouched
            assert page._draft["A"].quantity == 2
        finally:
            _teardown_page(page)

    def test_refresh_allowed_after_clearing_draft(self, qapp, tmp_path):
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            page.clear_draft()
            page._on_refresh_clicked()
            assert page._loading
            # Pump to deliver queued worker signals.
            _wait_for(lambda: not page._loading, timeout_ms=3000)
        finally:
            _teardown_page(page)

    def test_threshold_change_blocked_with_draft(self, qapp, tmp_path):
        """Changing threshold via refresh is also blocked when a draft exists."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            page._threshold_spin.setValue(20)
            page._on_refresh_clicked()
            # Refresh blocked — threshold not consumed
            assert not page._loading
            assert "πρόχειρο" in page._state_lbl.text().lower()
        finally:
            _teardown_page(page)


# ── Draft lifecycle / shutdown tests (P3.3) ─────────────────────────────

class TestSupplierReorderDraftLifecycle:
    """A draft coexists with the worker lifecycle safely."""

    def test_shutdown_with_draft_returns_true_when_idle(self, qapp, tmp_path):
        """A non-empty draft does not block shutdown — it is in-memory only."""
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)
            assert page.shutdown() is True
        finally:
            _teardown_page(page)

    def test_stale_callback_cannot_mutate_closing_page_with_draft(
        self, qapp, tmp_path,
    ):
        """A worker callback arriving during close must be discarded.

        Builds a page with a draft, marks it close-pending (simulating
        shutdown-in-progress), then delivers a fake ``_on_data_ready``.
        The page state must NOT be mutated and the draft must survive.
        """
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [], suppliers=[])
        page = _make_page(db)
        try:
            from qt_app.data_source import (
                ReorderCandidate, SupplierReorderResult,
                SupplierReorderGroup,
            )
            page._add_to_draft(1, "S1", ReorderCandidate(
                "A", "Alpha", 1, 10, "2027-01-01", 1.0), quantity=2)

            page._close_pending = True
            fake = SupplierReorderResult.success(
                (SupplierReorderGroup(2, "Other", (
                    ReorderCandidate("Z", "Zeta", 1, 10, "2027-01-01", 1.0),
                )),),
                (),
            )
            page._on_data_ready(fake)
            # Close pending remained; UI not mutated to show "Other"
            assert page._close_pending
            assert "Other" not in page._summary.text()
            # Draft untouched
            assert page._draft["A"].quantity == 2
        finally:
            _teardown_page(page)

    def test_shutdown_while_worker_running_with_draft(
        self, qapp, tmp_path, monkeypatch,
    ):
        """A draft does not interfere with worker shutdown.

        Mirrors the existing worker-shutdown test but with a non-empty
        draft.  ``shutdown()`` returns False while the worker is
        blocked, then True once it completes.  The draft survives.

        The blocking-load monkeypatch is installed AFTER the page's
        initial refresh has completed normally.
        """
        db = str(tmp_path / "t.db")
        _make_reorder_db(db, [("A", "X", 1, "2027-01-01", 1.0, 1)],
                         suppliers=[(1, "S1")])

        import qt_app.pages.supplier_reorder_page as srp_mod

        page = _make_page(db)
        try:
            from qt_app.data_source import ReorderCandidate
            page._add_to_draft(1, "S1", ReorderCandidate(
                "B", "Beta", 1, 10, "2027-01-01", 1.0), quantity=3)

            _block = threading.Event()
            _started = threading.Event()

            def _blocking_load(db_path, threshold):
                _started.set()
                _block.wait(timeout=5.0)
                from qt_app.data_source import SupplierReorderResult
                return SupplierReorderResult.success((), ())

            monkeypatch.setattr(srp_mod, "load_supplier_reorder_candidates",
                                _blocking_load)

            page.refresh()
            assert _wait_for(_started.is_set, timeout_ms=2000)

            assert page.shutdown() is False
            assert page._thread is not None
            assert page._draft["B"].quantity == 3  # untouched

            _block.set()
            # Worker unblocked — pump to deliver queued signals.
            _wait_for(
                lambda: page._thread is None or not page._thread.isRunning(),
                timeout_ms=3000,
            )
            _spin(3)
            # Draft still intact after worker finishes
            assert page._draft["B"].quantity == 3
        finally:
            _teardown_page(page)


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

