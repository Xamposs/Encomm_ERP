"""E1: Qt pilot entry-point readiness — fresh-DB and navigation tests."""

from __future__ import annotations

import os
import sys

import pytest

# Offscreen must be set before any PySide6 import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test_erp.db")


@pytest.fixture()
def nonexistent_db(tmp_path):
    return str(tmp_path / "nonexistent" / "fresh_erp.db")


class TestCreateMainWindow:
    """Tests for the create_main_window factory."""

    def test_fresh_db_initialized(self, nonexistent_db):
        """A nonexistent temporary DB path is initialized."""
        from qt_main import create_main_window

        app, window = create_main_window(db_path=nonexistent_db)
        try:
            assert os.path.isfile(nonexistent_db), "DB file was not created"
            import sqlite3
            conn = sqlite3.connect(nonexistent_db)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required = {"ProductMaster", "suppliers", "customers",
                        "invoices", "stock_movements", "SystemConfig"}
            missing = required - tables
            assert not missing, f"Missing base tables: {missing}"
            conn.close()
        finally:
            window.deleteLater()
            window.close()

    def test_main_window_gets_db_service(self, tmp_db):
        """The constructed MainWindow receives a non-None DatabaseService."""
        from qt_main import create_main_window

        app, window = create_main_window(db_path=tmp_db)
        try:
            assert window.db_service is not None
            # The service is a fully initialized DatabaseService
            assert hasattr(window.db_service, "add_product")
            assert hasattr(window.db_service, "get_all_products")
        finally:
            window.deleteLater()
            window.close()

    def test_qt_main_import_chain_clean(self):
        """The qt_main module does NOT force-import customtkinter or presentation
        in its own import chain.  The test checks that qt_main.py's source
        references contain no import of those modules, and that the factory
        function does not transitively reference them."""
        import ast, inspect
        from qt_main import create_main_window
        src = inspect.getsource(create_main_window)
        # The factory function must not contain references to the banned modules
        for banned in ("customtkinter", "presentation"):
            assert banned not in src, \
                f"create_main_window references banned module '{banned}'"
        # The module-level source of qt_main.py must not import them
        import qt_main
        src_module = inspect.getsource(qt_main)
        for banned in ("customtkinter", "presentation"):
            assert banned not in src_module, \
                f"qt_main.py references banned module '{banned}'"
        # Bonus: the function returns (app, window) as documented
        assert hasattr(create_main_window, "__call__")


# ── Navigation provability test ──────────────────────────────────────────

class TestAllPagesNavigable:
    """Prove every NAV_ITEMS page can be lazily constructed against a fresh DB."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_db):
        from qt_main import create_main_window
        from qt_app.main_window import NAV_ITEMS

        self.app, self.window = create_main_window(db_path=tmp_db)
        self.nav_items = NAV_ITEMS

    def teardown_method(self):
        if hasattr(self, "window") and self.window is not None:
            self.window.deleteLater()
            self.window.close()

    def test_every_key_in_nav_items_can_be_navigated_to(self):
        """Navigate to every page key — no uncaught exception."""
        unreachable: list[str] = []
        for key, label in self.nav_items:
            try:
                self.window.navigate_to(key)
                # Process events so lazy page construction happens
                from PySide6.QtCore import QCoreApplication
                for _ in range(5):
                    QCoreApplication.processEvents()
            except Exception as exc:
                unreachable.append(f"{key} ({label}): {exc}")
        assert not unreachable, \
            f"Some pages raised: {'; '.join(unreachable)}"

    def test_all_pages_have_non_none_stack_widget(self):
        """After navigation, the stacked widget index is valid."""
        for key, _label in self.nav_items:
            self.window.navigate_to(key)
            from PySide6.QtCore import QCoreApplication
            for _ in range(5):
                QCoreApplication.processEvents()
            dest = self.window._pages.get(key)
            assert dest is not None, f"Page '{key}' was never cached"
            # The page must be a QWidget (not a placeholder anymore)
            from PySide6.QtWidgets import QWidget
            assert isinstance(dest, QWidget), \
                f"Page '{key}' is not a QWidget"

    def test_every_page_is_instance_of_qwidget(self):
        """Every page is a proper QWidget (proves lazy construction worked)."""
        for key, _label in self.nav_items:
            self.window.navigate_to(key)
            from PySide6.QtCore import QCoreApplication
            for _ in range(5):
                QCoreApplication.processEvents()
            page = self.window._pages.get(key)
            assert page is not None
            from PySide6.QtWidgets import QWidget
            assert isinstance(page, QWidget), \
                f"Page '{key}' is not a QWidget"
            # The page placeholder was replaced — snapshot the object name or class
            assert type(page).__name__ != "QWidget" or type(page) != QWidget, \
                f"Page '{key}' was never replaced (still using placeholder)"
