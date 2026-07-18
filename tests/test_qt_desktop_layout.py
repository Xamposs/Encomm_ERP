"""F1 desktop UX tests — global click-to-blur and Goods Receipts layout modes.

Covers:
- Clicking blank content after focusing a QLineEdit removes focus but
  preserves the typed text.
- Clicking inside the focused QLineEdit keeps focus.
- Clicks are not swallowed — they still reach their intended target.
- The blur filter is scoped to its MainWindow and dies with it (no
  application-wide event-filter leakage after a window is destroyed).
- Goods Receipts: full-width list when no detail is active; selecting or
  creating reveals a responsive detail pane; returning/cancelling
  restores list-only mode; no worker thread survives shutdown.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QMessageBox

from qt_app.pages.goods_receipt_page import GoodsReceiptPage


# ── Helpers ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _drain_stale_events(qapp):
    """Flush queued leftovers of earlier test modules before we start.

    Earlier dialog tests may leave deferred deletions and late worker
    signals in the queue; delivering them mid-test (with real modal
    QMessageBoxes) crashes offscreen runs.  Drain them once, with modal
    dialogs neutralised.
    """
    mp = pytest.MonkeyPatch()
    for name, ret in (("warning", QMessageBox.Ok),
                      ("information", QMessageBox.Ok),
                      ("critical", QMessageBox.Ok),
                      ("question", QMessageBox.No)):
        mp.setattr(QMessageBox, name,
                   staticmethod(lambda *a, _r=ret, **k: _r))
    for _ in range(10):
        QCoreApplication.processEvents()
    mp.undo()
    yield


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Never allow real modal QMessageBoxes in offscreen tests.

    Also shields this module from stale queued signals of earlier test
    modules (e.g. a late worker delivering an error result while we spin
    the event loop) — a real modal box there would hang or crash.
    """
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))


def _spin(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


def _teardown_window(window) -> None:
    """Stop page workers, then close and destroy the window."""
    for page in list(getattr(window, "_pages", {}).values()):
        if hasattr(page, "shutdown"):
            try:
                page.shutdown()
            except Exception:
                pass
    _spin(3)
    window.close()
    window.deleteLater()
    _spin(3)
    # Deliver the deferred delete for THIS window only.  Never flush
    # app-wide (receiver=None): that would destroy other test modules'
    # pages while their worker threads are still emitting → heap
    # corruption on Windows.
    QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
    _spin(2)


@pytest.fixture()
def main_window(qapp, tmp_path):
    from qt_main import create_main_window
    _app, window = create_main_window(db_path=str(tmp_path / "f1_erp.db"))
    window.resize(1150, 730)
    window.show()
    window.activateWindow()
    _spin(10)
    yield window
    _teardown_window(window)


def _focus(widget) -> None:
    widget.setFocus(Qt.OtherFocusReason)
    _spin(5)


def _window_click(window, target_widget) -> None:
    """Click the CENTER of target_widget through the window's QWindow.

    Real user input enters through the platform window; widget-level
    QTest clicks bypass the window handle and would never exercise the
    window-scoped blur filter.
    """
    # Imported lazily: loading the QtTest DLL at collection time (before
    # the shared QApplication exists) destabilises Windows offscreen runs.
    from PySide6.QtTest import QTest
    handle = window.windowHandle()
    assert handle is not None, "window must be shown first"
    pos = target_widget.mapTo(window, target_widget.rect().center())
    QTest.mouseClick(handle, Qt.LeftButton, Qt.KeyboardModifiers(), pos)
    _spin(5)


# ══════════════════════════════════════════════════════════════════════
# 1. Global click-to-blur behaviour
# ══════════════════════════════════════════════════════════════════════

class TestClickToBlur:

    def test_blank_click_clears_focus_and_preserves_text(self, main_window):
        """Focused AI bar + click on blank header area → focus cleared,
        text intact, no page change."""
        bar = main_window._ai_cmd_bar
        bar.setText("δοκιμή αναζήτησης")
        _focus(bar)
        assert bar.hasFocus(), "AI bar should have focus after setFocus"

        page_before = main_window._current_page
        # The header title label is inert blank content.
        _window_click(main_window, main_window._title_lbl)

        assert not bar.hasFocus(), \
            "Clicking blank content must clear QLineEdit focus"
        assert bar.text() == "δοκιμή αναζήτησης", \
            "Clearing focus must not clear the text"
        assert main_window._current_page == page_before, \
            "Blur must not change the current page"

    def test_click_inside_focused_lineedit_keeps_focus(self, main_window):
        bar = main_window._ai_cmd_bar
        bar.setText("κείμενο")
        _focus(bar)
        assert bar.hasFocus()

        _window_click(main_window, bar)

        assert bar.hasFocus(), \
            "Clicking inside the focused QLineEdit must keep its focus"
        assert bar.text() == "κείμενο"

    def test_click_still_reaches_target(self, main_window):
        """The filter never swallows events: a click on a sidebar nav
        button both blurs the input AND performs the navigation."""
        bar = main_window._ai_cmd_bar
        _focus(bar)
        assert bar.hasFocus()

        inventory_btn = main_window._nav_group.button(
            main_window._nav_keys.index("inventory"))
        _window_click(main_window, inventory_btn)
        _spin(10)

        assert main_window._current_page == "inventory", \
            "The click must still reach its intended target (navigation)"
        assert not bar.hasFocus(), \
            "Focus must be cleared by the outside click"

    def test_page_search_field_blurs_on_blank_click(self, main_window):
        """Works for page-level search fields, not just the AI bar."""
        main_window.navigate_to("goods_receipts")
        _spin(10)
        page = main_window._pages["goods_receipts"]

        page._search.setText("ΔΑ-2026")
        _focus(page._search)
        assert page._search.hasFocus()

        _window_click(main_window, main_window._title_lbl)

        assert not page._search.hasFocus()
        assert page._search.text() == "ΔΑ-2026", \
            "Blur must not reset the search filter text"

    def test_filter_is_central_and_window_scoped(self, main_window):
        """One filter object, owned by the MainWindow (central, no
        per-page mouse handlers)."""
        filt = getattr(main_window, "_blur_filter", None)
        assert filt is not None, "MainWindow must own the blur filter"
        assert filt.parent() is main_window, \
            "Filter must be a child of its MainWindow (dies with it)"

        # No page defines its own mousePressEvent for blur handling.
        import inspect
        from qt_app.pages import PAGE_CLASSES
        for key, cls in PAGE_CLASSES.items():
            assert "mousePressEvent" not in cls.__dict__, \
                f"Page '{key}' must not duplicate blur mouse handlers"

    def test_no_filter_leak_after_window_destroyed(self, qapp, tmp_path):
        """Destroying the window destroys its filter — nothing global
        survives to act on later windows/events."""
        from qt_main import create_main_window
        _app, window = create_main_window(db_path=str(tmp_path / "leak.db"))
        window.show()
        _spin(5)

        destroyed = []
        window._blur_filter.destroyed.connect(lambda *_: destroyed.append(True))
        _teardown_window(window)

        assert destroyed, \
            "Blur filter must be destroyed together with its MainWindow"


# ══════════════════════════════════════════════════════════════════════
# 2. Goods Receipts layout modes
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def receipt_page(qapp, db):
    page = GoodsReceiptPage(None, {"db_path": db.db_path, "theme": "Dark"})
    page.resize(1100, 650)
    page.show()
    _spin(10)
    yield page
    page.shutdown()
    page.deleteLater()
    _spin(3)


def _fake_receipt(status: str = "draft"):
    from infrastructure.goods_receipt_service import (
        GoodsReceipt, GoodsReceiptLine, GetReceiptResult)
    rec = GoodsReceipt(
        id="GR-TEST-1", supplier_id=1, supplier_name="Demo Pharma AE",
        document_number="ΔΑ-2026-0042", document_type="delivery_note",
        status=status, received_at="2026-07-18", approved_at=None,
        approved_by=None, notes="", created_at="2026-07-18T10:00:00",
        lines=(GoodsReceiptLine(1, "5200000000017", "Παρακεταμόλη", 10, 1.5),),
    )
    return GetReceiptResult(ok=True, receipt=rec)


class TestGoodsReceiptLayoutModes:

    def test_list_mode_is_full_width(self, receipt_page):
        """No selected receipt → detail pane hidden, list takes the full
        page width, no placeholder detail text."""
        page = receipt_page
        assert not page._right_panel.isVisible(), \
            "Detail pane must be hidden in list mode"

        sizes = page._splitter.sizes()
        total = page._splitter.width()
        assert sizes[1] == 0, "Hidden detail pane must take no width"
        assert sizes[0] >= int(total * 0.9), \
            f"List must use full width in list mode (got {sizes[0]}/{total})"

        # The old 'select a receipt' placeholder is gone
        import inspect
        src = inspect.getsource(GoodsReceiptPage.build_ui)
        assert "Επιλέξτε παραλαβή" not in src, \
            "No giant empty placeholder pane in list mode"

    def test_no_horizontal_scrollbar_at_desktop_width(self, receipt_page):
        page = receipt_page
        hbar = page._list_table.horizontalScrollBar()
        assert not hbar.isVisible(), \
            "List table must not need a horizontal scrollbar at 1100px"

    def test_selecting_receipt_reveals_responsive_detail(self, receipt_page):
        """A loaded receipt detail reveals the splitter pane at sensible
        desktop proportions (~40-45% list / ~55-60% detail)."""
        page = receipt_page
        page._on_detail_result(_fake_receipt())
        _spin(5)

        assert page._right_panel.isVisible(), \
            "Detail pane must be revealed after selecting a receipt"
        assert page._right_stack.currentIndex() == 0  # review panel

        sizes = page._splitter.sizes()
        total = sizes[0] + sizes[1]
        assert total > 0
        ratio = sizes[0] / total
        assert 0.35 <= ratio <= 0.50, \
            f"List share in detail mode should be ~40-45% (got {ratio:.2f})"

    def test_new_receipt_reveals_editor(self, receipt_page):
        page = receipt_page
        page._on_suppliers_result([(1, "Demo Pharma AE")])
        _spin(5)

        assert page._right_panel.isVisible(), \
            "Detail pane must be revealed for a new receipt draft"
        assert page._right_stack.currentIndex() == 1  # editor panel

    def test_back_restores_full_width_list(self, receipt_page):
        page = receipt_page
        page._on_detail_result(_fake_receipt())
        _spin(3)
        assert page._right_panel.isVisible()

        page._back_to_list()
        _spin(3)

        assert not page._right_panel.isVisible(), \
            "Returning to list mode must hide the detail pane"
        sizes = page._splitter.sizes()
        assert sizes[1] == 0
        assert sizes[0] >= int(page._splitter.width() * 0.9), \
            "Full-width list must be restored after returning"
        assert page._selected_receipt_id is None

    def test_cancel_new_receipt_restores_full_width_list(self, receipt_page):
        page = receipt_page
        page._on_suppliers_result([(1, "Demo Pharma AE")])
        _spin(3)
        assert page._right_panel.isVisible()

        page._back_to_list()  # the editor's cancel button connects here
        _spin(3)

        assert not page._right_panel.isVisible()
        assert page._draft_lines == []

    def test_no_hardcoded_narrow_widths(self):
        """No fixed pixel splitter sizes or max-width caps on the list."""
        import inspect
        src = inspect.getsource(GoodsReceiptPage.build_ui)
        assert "setSizes([400, 650])" not in src, \
            "Hard-coded narrow splitter sizes must be removed"
        assert "setMaximumWidth" not in src, \
            "No max-width caps that keep the list tiny"

    def test_detail_panels_scroll_at_reduced_sizes(self, qapp, db):
        """At reduced window sizes the detail/editor panes are inside a
        QScrollArea — controls scroll instead of clipping."""
        from PySide6.QtWidgets import QScrollArea
        page = GoodsReceiptPage(None, {"db_path": db.db_path})
        try:
            page.resize(700, 420)
            page.show()
            _spin(5)
            for idx in (0, 1):
                wrapper = page._right_stack.widget(idx)
                assert isinstance(wrapper, QScrollArea), \
                    f"Detail stack index {idx} must be scroll-wrapped"
                assert wrapper.widgetResizable()
        finally:
            page.shutdown()
            page.deleteLater()
            _spin(3)

    def test_no_worker_thread_survives_shutdown(self, qapp, db):
        page = GoodsReceiptPage(None, {"db_path": db.db_path})
        page.show()
        _spin(5)
        # Exercise both modes, then shut down
        page._on_detail_result(_fake_receipt())
        page._back_to_list()
        assert page.shutdown() or page._thread is None
        thread = page._thread
        assert thread is None or not thread.isRunning(), \
            "No worker thread may survive page shutdown"
        page.deleteLater()
        _spin(3)

    def test_service_layer_untouched(self):
        """F1 is UI-only: receipt service, approval gate and validation
        are byte-identical in behaviour (no source edits)."""
        import subprocess
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=ROOT)
        changed = {l.strip() for l in out.stdout.splitlines() if l.strip()}
        banned = {
            "infrastructure/goods_receipt_service.py",
            "infrastructure/database_service.py",
            "core/business_rules.py",
        }
        assert not (changed & banned), \
            f"F1 must not modify service/business files: {changed & banned}"
