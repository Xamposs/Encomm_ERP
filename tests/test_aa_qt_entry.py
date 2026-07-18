"""E1: Qt pilot entry-point readiness — fresh-DB and navigation tests."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── Helpers ─────────────────────────────────────────────────────────────

def _teardown_window(window) -> None:
    """Safe window shutdown: stop all page workers, close, delete."""
    pages = getattr(window, "_pages", {})
    for page in list(pages.values()):
        if hasattr(page, "shutdown"):
            try:
                page.shutdown()
            except Exception:
                pass
    window.close()
    window.deleteLater()


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test_erp.db")


@pytest.fixture()
def nonexistent_db(tmp_path):
    return str(tmp_path / "nonexistent" / "fresh_erp.db")


class TestCreateMainWindow:
    """Prove the factory initializes a fresh DB and hands over a live service."""

    def test_fresh_db_initialized(self, qapp, nonexistent_db):
        """A nonexistent temporary DB path is initialized."""
        from qt_main import create_main_window

        _app, window = create_main_window(db_path=nonexistent_db)
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
            _teardown_window(window)

    def test_main_window_gets_db_service(self, qapp, tmp_db):
        """The constructed MainWindow receives a non-None DatabaseService."""
        from qt_main import create_main_window

        _app, window = create_main_window(db_path=tmp_db)
        try:
            assert window.db_service is not None
            assert hasattr(window.db_service, "add_product")
            assert hasattr(window.db_service, "get_all_products")
        finally:
            _teardown_window(window)

    def test_qt_main_import_chain_clean(self):
        """qt_main.py source does NOT reference customtkinter or presentation."""
        import inspect
        from qt_main import create_main_window

        src = inspect.getsource(create_main_window)
        for banned in ("customtkinter", "presentation"):
            assert banned not in src, \
                f"create_main_window references '{banned}'"

        import qt_main
        src_module = inspect.getsource(qt_main)
        for banned in ("customtkinter", "presentation"):
            assert banned not in src_module, \
                f"qt_main.py references '{banned}'"


# ── Navigation provability test ──────────────────────────────────────────

class TestAllPagesNavigable:
    """Prove every NAV_ITEMS page can be lazily constructed from a fresh DB,
    and no live QThread worker survives teardown."""

    @pytest.fixture(autouse=True)
    def _setup(self, qapp, tmp_db):
        from qt_main import create_main_window
        from qt_app.main_window import NAV_ITEMS

        _app, self.window = create_main_window(db_path=tmp_db)
        self.nav_items = NAV_ITEMS
        self._built_keys: list[str] = []  # track which pages were lazily created

    def teardown_method(self):
        if hasattr(self, "window") and self.window is not None:
            _teardown_window(self.window)

    def test_every_key_in_nav_items_can_be_navigated_to(self):
        """Navigate to every page key — no uncaught exception."""
        unreachable: list[str] = []
        for key, label in self.nav_items:
            try:
                self.window.navigate_to(key)
                page = self.window._pages.get(key)
                if page and hasattr(page, "shutdown"):
                    try:
                        page.shutdown()
                    except Exception:
                        pass
                self._built_keys.append(key)
            except Exception as exc:
                unreachable.append(f"{key} ({label}): {exc}")
        assert not unreachable, \
            f"Some pages raised: {'; '.join(unreachable)}"

    def test_all_pages_cached_and_are_real_widgets(self):
        """Each cached page is a proper widget, not a placeholder."""
        self._navigate_all()
        for key in self._built_keys:
            page = self.window._pages.get(key)
            assert page is not None, f"Page '{key}' was never cached"
            from PySide6.QtWidgets import QWidget
            assert isinstance(page, QWidget), \
                f"Page '{key}' is not a QWidget"

    def test_no_placeholder_qwidget_survives(self):
        """Every cached page is a specific page class, not a bare QWidget."""
        self._navigate_all()
        for key in self._built_keys:
            page = self.window._pages.get(key)
            assert page is not None
            from PySide6.QtWidgets import QWidget
            # A bare QWidget means the placeholder was never replaced
            assert type(page) is not QWidget, \
                f"Page '{key}' is still a placeholder (bare QWidget)"

    def test_no_live_thread_after_teardown(self):
        """After shutdown, no page still has a running QThread."""
        self._navigate_all()
        # Explicitly trigger teardown, then check
        _teardown_window(self.window)
        for key in self._built_keys:
            page = self.window._pages.get(key) if hasattr(self.window, "_pages") else None
            if page and hasattr(page, "_thread"):
                thread = page._thread
                if thread is not None and thread.isRunning():
                    pytest.fail(f"Page '{key}' still has a running QThread after teardown")

    def _navigate_all(self) -> None:
        # Navigate to each page, shut down its worker, then move on.
        for key, _label in self.nav_items:
            self.window.navigate_to(key)
            page = self.window._pages.get(key)
            if page and hasattr(page, "shutdown"):
                try:
                    page.shutdown()
                except Exception:
                    pass
            self._built_keys.append(key)
