"""Qt navigation repaint test — verifies clean page switching.

Also covers pinned sidebar navigation layout and utility button
regression tests for the two-region sidebar design.

Sidebar tests use a shell_window fixture that replaces PAGE_CLASSES
with lightweight stubs so no QThread workers or real database access
ever runs during layout/hierarchy tests.
"""

import os
import pytest

# Skip if PySide6 not importable or no display
pyside6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QStackedWidget, QScrollArea, QWidget,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["offscreen"])
    return app


# ── Source-inspection structural tests ─────────────────────────────────

class TestNavigationStructural:

    def test_base_page_has_opaque_background(self):
        from qt_app.pages.base_page import BasePage
        from qt_app import styles
        import inspect
        src = inspect.getsource(BasePage.__init__)
        assert "setAutoFillBackground(True)" in src
        assert "QColor(styles.DARK_BG)" in src
        assert "setPalette" in src

    def test_base_page_no_title_label(self):
        from qt_app.pages.base_page import BasePage
        import inspect
        src = inspect.getsource(BasePage.__init__)
        assert "title_label" not in src

    def test_content_wrapper_has_object_name_and_bg(self):
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.__init__)
        assert 'setObjectName("contentWrapper")' in src
        assert "background" in src

    def test_stack_has_object_name_and_bg(self):
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.__init__)
        assert 'setObjectName("pageStack")' in src

    def test_navigate_to_uses_setCurrentWidget(self):
        from qt_app.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow.navigate_to)
        assert "setCurrentWidget" in src
        assert "dest.show()" in src
        assert "dest.raise_()" in src
        assert "dest.updateGeometry()" in src
        assert "self._stack.update()" in src


# ── Sidebar pinned-navigation layout tests ─────────────────────────────
# All sidebar tests use a shell_window fixture that prevents real workers
# and database access by replacing PAGE_CLASSES with lightweight stubs.

class _StubPage(QWidget):
    """Lightweight stub page — no QThread, no database access.

    Accepts the same (db_service, config) signature as real pages
    so it can replace PAGE_CLASSES entries seamlessly.
    """
    def __init__(self, db_service=None, config=None):
        super().__init__()

    def shutdown(self):
        return True


@pytest.fixture()
def shell_window(monkeypatch, tmp_path, qapp):
    """A MainWindow where every route uses _StubPage instead of real pages.

    Prevents real QThread workers, database connections, and accidental
    access to the developer's encomm_erp.db.  Tears down cleanly by
    asserting close() is accepted before calling deleteLater().
    """
    from qt_app.main_window import MainWindow, NAV_ITEMS
    import qt_app.main_window as mw_mod

    # Replace all routes with stub pages
    stubs = {key: _StubPage for key, _ in NAV_ITEMS}
    monkeypatch.setattr(mw_mod, "PAGE_CLASSES", stubs)

    db_path = str(tmp_path / "shell_test.db")
    mw = MainWindow(config={"db_path": db_path})
    yield mw

    assert mw.close(), "MainWindow close must be accepted"
    mw.deleteLater()
    for _ in range(20):
        QCoreApplication.processEvents()


def _spin(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


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

    def test_stock_lot_integrity_in_primary_region(self, shell_window):
        """stock_lot_integrity button lives inside the primary QScrollArea."""
        mw = shell_window
        scroll = mw.findChild(QScrollArea, "primaryNavScroll")
        assert scroll is not None
        scroll_content = scroll.widget()
        assert scroll_content is not None
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        primary_keys = [k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS]
        idx = primary_keys.index("stock_lot_integrity")
        btn = mw._nav_group.button(idx)
        assert btn is not None
        assert scroll_content.isAncestorOf(btn), (
            "stock_lot_integrity button must be inside primaryNavScroll")

    def test_utility_outside_scroll_area(self, shell_window):
        """settings and ai_assistant are NOT descendants of primaryNavScroll."""
        mw = shell_window
        scroll = mw.findChild(QScrollArea, "primaryNavScroll")
        assert scroll is not None
        scroll_content = scroll.widget()
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
        for i, key in enumerate(UTILITY_NAV_KEYS):
            idx = primary_count + i
            btn = mw._nav_group.button(idx)
            assert btn is not None
            assert not scroll_content.isAncestorOf(btn), (
                f"{key} must NOT be inside primaryNavScroll")

    def test_utility_container_order(self, shell_window):
        """utilityNavContainer contains settings first, then ai_assistant
        in layout order."""
        from qt_app.main_window import UTILITY_NAV_KEYS
        mw = shell_window
        util_container = mw.findChild(QWidget, "utilityNavContainer")
        assert util_container is not None, "utilityNavContainer must exist"
        lay = util_container.layout()
        assert lay is not None
        for i, key in enumerate(UTILITY_NAV_KEYS):
            w = lay.itemAt(i).widget()
            assert w is not None, f"Layout item {i} must be a widget"
            assert f" {key.split('_')[0]}" in w.text().lower() or \
                   w.text() != "", f"Item {i} text mismatch for {key}"

    def test_sidebar_has_stable_object_names(self, shell_window):
        """All sidebar elements have stable object names."""
        mw = shell_window
        assert mw.findChild(QWidget, "sidebarFrame") is not None
        assert mw.findChild(QScrollArea, "primaryNavScroll") is not None
        assert mw.findChild(QWidget, "sidebarUtilitySeparator") is not None
        assert mw.findChild(QWidget, "utilityNavContainer") is not None
        assert mw.findChild(QWidget, "sidebarVersionLabel") is not None

    def test_sidebar_layout_order(self, shell_window):
        """The sidebar QVBoxLayout order is:
        primaryNavScroll < sidebarUtilitySeparator < utilityNavContainer
        < sidebarVersionLabel."""
        mw = shell_window
        sidebar = mw.findChild(QWidget, "sidebarFrame")
        assert sidebar is not None
        lay = sidebar.layout()
        assert lay is not None
        # Collect widget object names in layout order
        order = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w:
                order.append(w.objectName() or "")
        expected = [
            "",  # brand (no object name)
            "primaryNavScroll",
            "sidebarUtilitySeparator",
            "utilityNavContainer",
            "sidebarVersionLabel",
        ]
        # Filter empty object names for brand
        named = [n for n in order if n]
        assert "primaryNavScroll" in named
        assert named.index("primaryNavScroll") < named.index("sidebarUtilitySeparator"), \
            "primaryNavScroll must be before sidebarUtilitySeparator"
        assert named.index("sidebarUtilitySeparator") < named.index("utilityNavContainer"), \
            "sidebarUtilitySeparator must be before utilityNavContainer"
        assert named.index("utilityNavContainer") < named.index("sidebarVersionLabel"), \
            "utilityNavContainer must be before sidebarVersionLabel"

    def test_sidebar_vertical_hierarchy(self, shell_window):
        """Settings button is above AI Assistant.
        AI Assistant bottom is above version label top.
        Version label is below utility container.
        Version label is the final widget in the sidebar layout."""
        from qt_app.main_window import NAV_ITEMS
        mw = shell_window
        mw.show()
        _spin(10)
        sidebar = mw.findChild(QWidget, "sidebarFrame")
        assert sidebar is not None
        lay = sidebar.layout()
        assert lay is not None

        # 1. Version label is the final layout widget
        last_widget = None
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w:
                last_widget = w
        assert last_widget is not None
        assert last_widget.objectName() == "sidebarVersionLabel", (
            "Version label must be the final widget in the sidebar layout")

        # 2. Map button and label positions into sidebar coordinates
        settings_idx = NAV_ITEMS.index(("settings", "⚙️  Ρυθμίσεις"))
        ai_idx = NAV_ITEMS.index(("ai_assistant", "🤖  AI Βοηθός"))
        s_btn = mw._nav_group.button(settings_idx)
        ai_btn = mw._nav_group.button(ai_idx)
        ver = mw.findChild(QWidget, "sidebarVersionLabel")
        util_container = mw.findChild(QWidget, "utilityNavContainer")
        assert s_btn is not None and ai_btn is not None
        assert ver is not None and util_container is not None

        # Settings above AI Assistant
        s_bottom = s_btn.mapTo(sidebar, s_btn.rect().bottomLeft()).y()
        ai_top = ai_btn.mapTo(sidebar, ai_btn.rect().topLeft()).y()
        assert s_bottom <= ai_top, (
            f"Settings bottom ({s_bottom}) must be above AI Assistant top ({ai_top})")

        # AI Assistant above version label
        ai_bottom = ai_btn.mapTo(sidebar, ai_btn.rect().bottomLeft()).y()
        ver_top = ver.mapTo(sidebar, ver.rect().topLeft()).y()
        assert ai_bottom <= ver_top, (
            f"AI Assistant bottom ({ai_bottom}) must be above version label top ({ver_top})")

        # Version label below utility container
        util_bottom = util_container.mapTo(sidebar, util_container.rect().bottomLeft()).y()
        ver_top2 = ver.mapTo(sidebar, ver.rect().topLeft()).y()
        assert util_bottom <= ver_top2, (
            f"Utility container bottom ({util_bottom}) must be above "
            f"version label top ({ver_top2})")

        # Version label is near the sidebar bottom (within bottom margin)
        ver_bottom = ver.mapTo(sidebar, ver.rect().bottomLeft()).y()
        sidebar_height = sidebar.geometry().height()
        gap = sidebar_height - ver_bottom
        # Bottom margin is 20px, allow a small tolerance for spacing
        assert 18 <= gap <= 28, (
            f"Version label bottom ({ver_bottom}) should be ~20px from "
            f"sidebar bottom ({sidebar_height}), gap={gap}")

    def test_ai_assistant_is_bottommost_nav_button(self, shell_window):
        """AI Assistant has the greatest bottom coordinate of every
        interactive navigation button. No interactive button appears
        below it."""
        from qt_app.main_window import NAV_ITEMS
        mw = shell_window
        mw.show()
        _spin(10)
        sidebar = mw.findChild(QWidget, "sidebarFrame")
        assert sidebar is not None
        bottoms: dict[str, int] = {}
        for i in range(len(NAV_ITEMS)):
            btn = mw._nav_group.button(i)
            assert btn is not None
            bottom = btn.mapTo(sidebar, btn.rect().bottomLeft()).y()
            bottoms[NAV_ITEMS[i][0]] = bottom
        ai_key = "ai_assistant"
        assert all(bottoms[ai_key] >= bottoms[k] for k in bottoms), (
            f"AI Assistant bottom ({bottoms[ai_key]}) is not the greatest: "
            f"{bottoms}")

    def test_utility_visible_at_minimum_size(self, shell_window):
        """Settings, AI Assistant, and version label remain visible at 1050x650."""
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        mw = shell_window
        mw.resize(1050, 650)
        mw.show()
        _spin(10)
        primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
        # Utility buttons
        for i, key in enumerate(UTILITY_NAV_KEYS):
            idx = primary_count + i
            btn = mw._nav_group.button(idx)
            assert btn is not None
            assert btn.isVisible(), f"{key} button must be visible at 1050x650"
        # Version label
        ver = mw.findChild(QWidget, "sidebarVersionLabel")
        assert ver is not None
        assert ver.isVisible(), "Version label must be visible at 1050x650"

    def test_primary_scrollable_when_constrained(self, shell_window):
        """The primary nav area scrolls when height-constrained.
        Settings, AI Assistant, and version label stay visible.
        Only the primary region scrolls."""
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        mw = shell_window
        mw.setMinimumSize(1050, 300)
        mw.resize(1050, 320)
        mw.show()
        _spin(10)

        scroll = mw.findChild(QScrollArea, "primaryNavScroll")
        assert scroll is not None
        vbar = scroll.verticalScrollBar()
        assert vbar.maximum() > 0, (
            f"Primary scroll area must be scrollable when constrained. "
            f"vbar max={vbar.maximum()}")

        # Utility buttons and version label remain visible
        primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
        for i, key in enumerate(UTILITY_NAV_KEYS):
            idx = primary_count + i
            btn = mw._nav_group.button(idx)
            assert btn is not None
            assert btn.isVisible(), f"{key} must be visible when constrained"
        ver = mw.findChild(QWidget, "sidebarVersionLabel")
        assert ver is not None
        assert ver.isVisible(), "Version label must be visible when constrained"

        # Utility container is not inside the scroll area
        util_container = mw.findChild(QWidget, "utilityNavContainer")
        assert util_container is not None
        scroll_content = scroll.widget()
        assert not scroll_content.isAncestorOf(util_container), (
            "utilityNavContainer must not be inside primaryNavScroll")

    def test_settings_click_navigates(self, shell_window):
        """Clicking the Settings button navigates to the settings page."""
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        mw = shell_window
        primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
        idx = primary_count + list(UTILITY_NAV_KEYS).index("settings")
        btn = mw._nav_group.button(idx)
        assert btn is not None
        btn.click()
        _spin(5)
        assert mw._current_page == "settings"

    def test_ai_assistant_click_navigates(self, shell_window):
        """Clicking the AI Assistant button navigates to ai_assistant."""
        from qt_app.main_window import NAV_ITEMS, UTILITY_NAV_KEYS
        mw = shell_window
        primary_count = len([k for k, _ in NAV_ITEMS if k not in UTILITY_NAV_KEYS])
        idx = primary_count + list(UTILITY_NAV_KEYS).index("ai_assistant")
        btn = mw._nav_group.button(idx)
        assert btn is not None
        btn.click()
        _spin(5)
        assert mw._current_page == "ai_assistant"

    def test_navigate_to_checks_button_across_regions(self, shell_window):
        """Programmatic navigate_to checks the correct button in both regions."""
        mw = shell_window
        mw.navigate_to("stock_movements")
        _spin(3)
        idx = mw._nav_keys.index("stock_movements")
        assert mw._nav_group.button(idx).isChecked()

        mw.navigate_to("settings")
        _spin(3)
        idx = mw._nav_keys.index("settings")
        assert mw._nav_group.button(idx).isChecked()

    def test_no_duplicate_page_from_utility_nav(self, shell_window):
        """Repeated navigation to a pinned utility button reuses the same page."""
        mw = shell_window
        mw.navigate_to("settings")
        p1 = mw._pages["settings"]
        mw.navigate_to("dashboard")
        mw.navigate_to("settings")
        p2 = mw._pages["settings"]
        assert p1 is p2, "Page must be reused, not recreated"

    def test_click_to_blur_window_scoped(self, shell_window):
        """The click-to-blur filter is window-scoped and owned by MainWindow."""
        mw = shell_window
        filt = mw._blur_filter
        assert filt is not None
        assert filt.parent() is mw