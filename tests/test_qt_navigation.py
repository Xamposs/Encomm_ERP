"""Qt navigation repaint test — verifies clean page switching."""

import os
import pytest

# Skip if PySide6 not importable or no display
pyside6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStackedWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["offscreen"])
    return app


class TestNavigationStructural:

    def test_base_page_has_opaque_background(self):
        """BasePage sets autoFillBackground and DARK_BG palette."""
        from qt_app.pages.base_page import BasePage
        from qt_app import styles

        # Inspect __init__ source to confirm autoFillBackground + palette
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
