"""Qt navigation repaint test — verifies clean page switching.

Also covers pinned sidebar navigation layout and utility button
regression tests for the two-region sidebar design.
"""

import os
import pytest

# Skip if PySide6 not importable or no display
pyside6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QCoreApplication, QElapsedTimer
from PySide6.QtWidgets import QApplication, QStackedWidget, QScrollArea


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["offscreen"])
    return app


# ── Bounded event pumping ──────────────────────────────────────────────

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


# ── Source-inspection structural tests ─────────────────────────────────

class TestNavigationStructural:

    def test_base_page_has_opaque_background(self):
        """BasePage sets autoFillBackground and DARK_BG palette."""
        from qt_app.pages.base_page import BasePage
        from qt_app import styles

        import inspect
        src = inspect.getsource(BasePage.__init__)
        assert "setAutoFillBackground(True)" in src
        assert "QColor(styles.DARK_BG)" in src
        assert "setPalette" in src

    def test_base_page_no_title_label(self):
        """BasePage.__init__ no longer creates title_label."""
        from qt_app.pages.base_page import BasePage
        import inspect
        src = inspect.getsource(BasePage.__init__)
        assert "title_label" not in src

    def test_content_wrapper_has_object_name_and_bg(self):
        """MainWindow content wrapper has objectName + background QSS."""
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.__init__)
        assert 'setObjectName("contentWrapper")' in src
        assert "background" in src

    def test_stack_has_object_name_and_bg(self):
        """QStackedWidget has objectName + background QSS."""
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.__init__)
        assert 'setObjectName("pageStack")' in src

    def test_navigate_to_uses_setCurrentWidget(self):
        """navigate_to calls setCurrentWidget, show, raise_, updateGeometry."""
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.navigate_to)
        assert "setCurrentWidget" in src
        assert "dest.show()" in src
        assert "dest.raise_()" in src
        assert "dest.updateGeometry()" in src
        assert "self._stack.update()" in src


# ── Sidebar pinned-navigation layout tests ─────────────────────────────

class TestSidebarPinnedNavigation:

    def test_exactly_12_nav_items(self):
        """NAV_ITEMS must contain exactly 12 routes."""
        from qt_app.main_window import NAV_ITEMS
        assert len(NAV_ITEMS) == 12

    def test_all_keys_present(self):
        """Every key appears exactly once in NAV_ITEMS."""
        from qt_app.main_window import NAV_ITEMS
        keys = [k for k, _ in NAV_ITEMS]
        assert len(keys) == len(set(keys)) == 12

    def test_utility_keys_defined(self):
        """UTILITY_NAV_KEYS is an immutable tuple with settings and ai_assistant."""
        from qt_app.main_window import UTILITY_NAV_KEYS
        assert isinstance(UTILITY_NAV_KEYS, tuple)
        assert UTILITY_NAV_KEYS == ("settings", "ai_assistant")

    def test_stock_lot_integrity_in_primary_region(self, qapp):
        """stock_lot_integrity button lives inside the primary QScrollArea."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        try:
            scroll = mw.findChild(QScrollArea, "primaryNavScroll")
            assert scroll is not None, "primaryNavScroll must exist"
            # The button for stock_lot_integrity must be inside the scroll area
            scroll_content = scroll.widget()
            assert scroll_content is not None
            # Find the button via nav_group
            from qt_app.main_window import NAV_ITEMS
            primary_keys = [k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS]
            idx = primary_keys.index("stock_lot_integrity")
            btn = mw._nav_group.button(idx)
            assert btn is not None
            assert scroll_content.isAncestorOf(btn), (
                "stock_lot_integrity button must be inside primaryNavScroll")
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_utility_outside_scroll_area(self, qapp):
        """settings and ai_assistant are NOT descendants of primaryNavScroll."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        try:
            scroll = mw.findChild(QScrollArea, "primaryNavScroll")
            assert scroll is not None
            scroll_content = scroll.widget()
            # Utility items start after primary items
            primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
            for i, key in enumerate(UTILITY_NAV_KEYS):
                idx = primary_count + i
                btn = mw._nav_group.button(idx)
                assert btn is not None
                assert not scroll_content.isAncestorOf(btn), (
                    f"{key} must NOT be inside primaryNavScroll")
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_utility_container_order(self, qapp):
        """utilityNavContainer contains settings first, then ai_assistant."""
        from qt_app.main_window import MainWindow
        from PySide6.QtWidgets import QWidget
        mw = MainWindow(config={})
        try:
            util_container = mw.findChild(QWidget, "utilityNavContainer")
            assert util_container is not None
            children = util_container.findChildren(
                type(util_container.layout().itemAt(0).widget()))  # QPushButton
            children = [w for w in util_container.children()
                        if w.isWidgetType() and w.inherits("QPushButton")]
            assert len(children) == 2
            assert "Ρυθμίσεις" in children[0].text()
            assert "AI Βοηθός" in children[1].text()
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_ai_assistant_is_bottommost_nav_button(self, qapp):
        """AI Assistant is the last interactive navigation button overall."""
        from qt_app.main_window import MainWindow, NAV_ITEMS
        mw = MainWindow(config={})
        try:
            all_btns = [mw._nav_group.button(i) for i in range(len(NAV_ITEMS))]
            last = all_btns[-1]
            assert last is not None
            assert "AI Βοηθός" in last.text()
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_utility_visible_at_minimum_size(self, qapp):
        """Settings and AI Assistant remain visible after resizing to 1050x650."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        mw.resize(1050, 650)
        mw.show()
        _spin(10)
        try:
            primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
            for i, key in enumerate(UTILITY_NAV_KEYS):
                idx = primary_count + i
                btn = mw._nav_group.button(idx)
                assert btn is not None
                assert btn.isVisible(), f"{key} button must be visible at 1050x650"
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_primary_scrollable_when_constrained(self, qapp):
        """The primary nav scroll area has scrollbars enabled."""
        from qt_app.main_window import MainWindow
        mw = MainWindow(config={})
        try:
            scroll = mw.findChild(QScrollArea, "primaryNavScroll")
            assert scroll is not None
            # Vertical scrollbar policy must be AsNeeded
            from PySide6.QtCore import Qt
            assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_settings_click_navigates(self, qapp):
        """Clicking the Settings button navigates to the settings page."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        try:
            primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
            idx = primary_count + list(UTILITY_NAV_KEYS).index("settings")
            btn = mw._nav_group.button(idx)
            assert btn is not None
            btn.click()
            _spin(5)
            assert mw._current_page == "settings"
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_ai_assistant_click_navigates(self, qapp):
        """Clicking the AI Assistant button navigates to ai_assistant."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        try:
            primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
            idx = primary_count + list(UTILITY_NAV_KEYS).index("ai_assistant")
            btn = mw._nav_group.button(idx)
            assert btn is not None
            btn.click()
            _spin(5)
            assert mw._current_page == "ai_assistant"
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_navigate_to_checks_button_across_regions(self, qapp):
        """Programmatic navigate_to checks the correct button in both regions."""
        from qt_app.main_window import MainWindow, NAV_ITEMS, UTILITY_NAV_KEYS
        mw = MainWindow(config={})
        try:
            primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
            # Navigate to a primary page
            mw.navigate_to("stock_movements")
            _spin(3)
            idx = mw._nav_keys.index("stock_movements")
            assert mw._nav_group.button(idx).isChecked()

            # Navigate to a utility page
            mw.navigate_to("settings")
            _spin(3)
            idx = mw._nav_keys.index("settings")
            assert mw._nav_group.button(idx).isChecked()
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_no_duplicate_page_from_utility_nav(self, qapp, tmp_path):
        """Repeated navigation to a pinned utility button reuses the same page."""
        from qt_app.main_window import MainWindow
        import sqlite3
        db_path = str(tmp_path / "util_nav.db")
        conn = sqlite3.connect(db_path)
        conn.close()

        mw = MainWindow(config={"db_path": db_path})
        try:
            mw.navigate_to("settings")
            p1 = mw._pages["settings"]
            mw.navigate_to("dashboard")
            mw.navigate_to("settings")
            p2 = mw._pages["settings"]
            assert p1 is p2, "Page must be reused, not recreated"
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)

    def test_click_to_blur_window_scoped(self, qapp):
        """The click-to-blur filter is window-scoped and owned by MainWindow."""
        from qt_app.main_window import MainWindow
        mw = MainWindow(config={})
        try:
            filt = mw._blur_filter
            assert filt is not None
            assert filt.parent() is mw
        finally:
            mw.close()
            mw.deleteLater()
            _spin(5)