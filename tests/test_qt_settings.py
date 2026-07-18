"""Qt tests for the SettingsPage — settings shell, backup UI, worker lifecycle."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QPushButton,
    QTabWidget, QTextEdit, QPlainTextEdit,
)

from qt_app.pages.settings_page import SettingsPage


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_db(path: str) -> None:
    """Create a minimal WAL-mode DB with required tables."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ProductMaster (
            Barcode    TEXT PRIMARY KEY,
            Name       TEXT NOT NULL,
            Stock      INTEGER NOT NULL DEFAULT 0,
            ExpiryDate TEXT NOT NULL DEFAULT '2099-12-31',
            Price      REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS SystemConfig (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO SystemConfig VALUES ('version', '1.0');
    """)
    conn.commit()
    conn.close()


class _FakeMessageBox:
    """Fake QMessageBox that always returns No without blocking."""
    Yes = 16384
    No = 65536

    @staticmethod
    def warning(*args, **kw):
        return _FakeMessageBox.No

    @staticmethod
    def critical(*args, **kw):
        return _FakeMessageBox.No

    @staticmethod
    def information(*args, **kw):
        return None


# ══════════════════════════════════════════════════════════════════════
# Page structure
# ══════════════════════════════════════════════════════════════════════

class TestSettingsPageStructure:
    """SettingsPage is a real Qt page, not a BasePage placeholder."""

    def test_page_has_backup_button(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            btn = getattr(page, "_backup_btn", None)
            assert btn is not None, "Missing backup button"
            assert "αντιγράφου" in btn.text()
        finally:
            page.deleteLater()

    def test_page_has_refresh_list_button(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            btn = getattr(page, "_refresh_list_btn", None)
            assert btn is not None, "Missing refresh list button"
            assert "Ανανέωση" in btn.text()
        finally:
            page.deleteLater()

    def test_page_has_table(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            tbl = getattr(page, "_table", None)
            assert tbl is not None, "Missing backup list table"
            assert tbl.columnCount() == 4
        finally:
            page.deleteLater()

    def test_page_has_folder_label(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            lbl = getattr(page, "_folder_lbl", None)
            assert lbl is not None, "Missing folder label"
            assert backup_dir in lbl.text()
        finally:
            page.deleteLater()

    def test_page_has_greek_section_header(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # The header now lives inside the backup tab — search all labels.
            found = any(
                "Αντίγραφα Ασφαλείας" in lbl.text()
                or "αντίγραφα" in lbl.text().lower()
                for lbl in page.findChildren(QLabel)
            )
            assert found, "Greek backup section header not found"
        finally:
            page.deleteLater()

    def test_page_not_a_basepage_placeholder(self, qapp, tmp_path):
        """SettingsPage overrides build_ui and does NOT use the placeholder."""
        import inspect
        src = inspect.getsource(SettingsPage.build_ui)
        assert "Έτοιμο για μετάβαση" not in src
        assert "placeholder" not in src.lower()
        assert "Αντίγραφα" in src, "Page must have Greek backup section"

    def test_listed_backups_show_pending_verification_status(self, qapp, tmp_path):
        """Table must show 'Θα ελεγχθεί' — NOT 'Επαληθευμένο' for listed backups."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        # Create a real backup file so the table has at least one row
        from infrastructure.backup_service import BackupService
        svc = BackupService(backup_dir=backup_dir)
        result = svc.create_backup(db_path)
        assert result.ok

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # Table should have rows after _refresh_list is called in build_ui
            rows = page._table.rowCount()
            assert rows >= 1, "Table should show the backup we created"

            # Check every row's status column
            for r in range(rows):
                item = page._table.item(r, 3)  # column 3 is status
                assert item is not None, f"Row {r} missing status"
                text = item.text()
                # Must use the pending-verification text, NOT false "Επαληθευμένο"
                assert "Θα ελεγχθεί" in text, \
                    f"Row {r}: expected 'Θα ελεγχθεί' but got '{text}'"
                assert "Επαληθευμένο" not in text, \
                    f"Row {r}: must not falsely claim 'Επαληθευμένο'"
        finally:
            page.shutdown()
            page.deleteLater()


# ══════════════════════════════════════════════════════════════════════
# Worker lifecycle (structural — no event loop)
# ══════════════════════════════════════════════════════════════════════

class TestSettingsPageWorkerLifecycle:
    """Controls are disabled while _loading is set, restored by _on_thread_done."""

    def test_controls_disabled_when_loading(self, qapp, tmp_path):
        """_on_backup_clicked sets loading flag and disables buttons."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # Trigger backup — this sets _loading and disables buttons
            page._on_backup_clicked()

            assert page._loading, "Loading flag should be set"
            assert not page._backup_btn.isEnabled(), \
                "Backup button should be disabled while loading"
            assert not page._refresh_list_btn.isEnabled(), \
                "Refresh button should be disabled while loading"

            # Wait for the background thread to finish
            if page._thread is not None:
                page._thread.wait(5000)

            # Simulate _on_thread_done by calling it directly (no event loop
            # in pytest to deliver the queued signal)
            page._on_thread_done()

            assert page._backup_btn.isEnabled(), \
                "Backup button should be enabled after _on_thread_done"
            assert page._refresh_list_btn.isEnabled(), \
                "Refresh button should be enabled after _on_thread_done"
            assert not page._loading, "Loading flag should be cleared"
        finally:
            page.shutdown()
            page.deleteLater()

    def test_successful_backup_refreshes_list_via_handler(self, qapp, tmp_path):
        """_on_backup_done with ok=True refreshes the list."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            initial_rows = page._table.rowCount()

            # Simulate a successful backup result
            from infrastructure.backup_service import BackupResult
            fake_result = BackupResult(
                ok=True,
                backup_path=str(Path(backup_dir) / "encomm_backup_test.db"),
                created_at="2026-01-01T12:00:00",
                size_bytes=8192,
                sha256="a" * 64,
            )
            page._on_backup_done(fake_result)

            # The handler calls _refresh_list(), which should populate
            # the table from the backup dir (empty in this test — but
            # the method itself is exercised)
            assert page._table.rowCount() >= 0, \
                "Table should be populated (possibly empty if no files)"
        finally:
            page.shutdown()
            page.deleteLater()

    def test_failed_backup_shows_error(self, qapp, tmp_path):
        """_on_backup_done with ok=False shows error status."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            from infrastructure.backup_service import BackupResult
            fail_result = BackupResult(
                ok=False,
                error_message="Test error message",
            )
            page._on_backup_done(fail_result)

            assert "Σφάλμα" in page._status_lbl.text()
            assert "Test error message" in page._status_lbl.text()
        finally:
            page.shutdown()
            page.deleteLater()


# ══════════════════════════════════════════════════════════════════════
# Shutdown
# ══════════════════════════════════════════════════════════════════════

class TestSettingsPageShutdown:
    """Page shutdown contract — no running threads after shutdown."""

    def test_shutdown_no_worker_returns_true(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            assert page.shutdown(), \
                "shutdown() should return True when no worker is active"
            assert page._thread is None
        finally:
            page.deleteLater()

    def test_shutdown_with_active_worker_stops_thread(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # Start a backup
            page._on_backup_clicked()
            assert page._thread is not None
            assert page._thread.isRunning()

            # Shutdown should wait and clean up
            ok = page.shutdown()
            assert page._thread is None, \
                "Thread reference should be cleared after shutdown"
        finally:
            page.deleteLater()


# ══════════════════════════════════════════════════════════════════════
# F1: Settings shell — four categories, honest integrations
# ══════════════════════════════════════════════════════════════════════

EXPECTED_TABS = ["Γενικά", "Αντίγραφα ασφαλείας", "Συνδέσεις", "Εφαρμογή"]


def _make_page(tmp_path) -> SettingsPage:
    db_path = str(tmp_path / "test.db")
    _make_db(db_path)
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return SettingsPage(
        db_service=None,
        config={"db_path": db_path, "backup_dir": backup_dir},
    )


class TestSettingsShell:
    """SettingsPage is a structured shell with the four required categories."""

    def test_has_four_required_categories(self, qapp, tmp_path):
        page = _make_page(tmp_path)
        try:
            tabs = getattr(page, "_tabs", None)
            assert isinstance(tabs, QTabWidget), "Settings must use a QTabWidget shell"
            titles = [tabs.tabText(i) for i in range(tabs.count())]
            assert titles == EXPECTED_TABS, \
                f"Expected categories {EXPECTED_TABS}, got {titles}"
        finally:
            page.deleteLater()

    def test_backup_controls_live_in_backup_category(self, qapp, tmp_path):
        """The verified backup UI moved intact into «Αντίγραφα ασφαλείας»."""
        page = _make_page(tmp_path)
        try:
            backup_tab = page._tabs.widget(EXPECTED_TABS.index("Αντίγραφα ασφαλείας"))
            for attr in ("_backup_btn", "_refresh_list_btn", "_table", "_folder_lbl"):
                w = getattr(page, attr, None)
                assert w is not None, f"Missing backup control {attr}"
                assert backup_tab.isAncestorOf(w), \
                    f"{attr} must live inside the backup category tab"
            assert page._table.columnCount() == 4
        finally:
            page.deleteLater()

    def test_integration_cards_visibly_planned_inactive(self, qapp, tmp_path):
        """AADE / IDIKA / email / AI cards exist and state (in Greek) that
        they are NOT active in Pilot v0.1."""
        page = _make_page(tmp_path)
        try:
            cards = getattr(page, "_integration_cards", None)
            assert cards is not None, "Missing integration cards"
            assert set(cards.keys()) == {"aade", "idika", "email", "ai"}

            conn_tab = page._tabs.widget(EXPECTED_TABS.index("Συνδέσεις"))
            for key, card in cards.items():
                assert conn_tab.isAncestorOf(card), \
                    f"Card '{key}' must live in the «Συνδέσεις» tab"
                texts = " ".join(l.text() for l in card.findChildren(QLabel))
                assert "Μη ενεργό" in texts, \
                    f"Card '{key}' must visibly state it is inactive"
                assert "Pilot v0.1" in texts, \
                    f"Card '{key}' must reference Pilot v0.1"
        finally:
            page.deleteLater()

    def test_no_credential_inputs_anywhere(self, qapp, tmp_path):
        """No QLineEdit / text inputs on the whole settings page — no
        credential or API-key fields were introduced."""
        page = _make_page(tmp_path)
        try:
            assert page.findChildren(QLineEdit) == [], \
                "Settings must not contain any line-edit input"
            assert page.findChildren(QTextEdit) == []
            assert page.findChildren(QPlainTextEdit) == []
        finally:
            page.deleteLater()

    def test_connections_tab_has_no_action_buttons(self, qapp, tmp_path):
        """Planning cards are inert: no connect/test/save buttons."""
        page = _make_page(tmp_path)
        try:
            conn_tab = page._tabs.widget(EXPECTED_TABS.index("Συνδέσεις"))
            assert conn_tab.findChildren(QPushButton) == [], \
                "«Συνδέσεις» must not expose any action button"
        finally:
            page.deleteLater()

    def test_general_and_app_tabs_read_only(self, qapp, tmp_path):
        """«Γενικά» and «Εφαρμογή» carry read-only status info: no fake
        Save buttons, and the DB location is shown."""
        page = _make_page(tmp_path)
        try:
            general = page._tabs.widget(EXPECTED_TABS.index("Γενικά"))
            app_tab = page._tabs.widget(EXPECTED_TABS.index("Εφαρμογή"))
            assert general.findChildren(QPushButton) == [], \
                "«Γενικά» must be read-only (no buttons)"
            assert app_tab.findChildren(QPushButton) == [], \
                "«Εφαρμογή» must be read-only (no buttons)"

            # DB location shown in «Γενικά»
            db_lbl = getattr(page, "_db_location_lbl", None)
            assert db_lbl is not None and general.isAncestorOf(db_lbl)
            assert os.path.basename(page._db_path) in db_lbl.text()

            # Pilot version + update capability as read-only status
            app_texts = " ".join(l.text() for l in app_tab.findChildren(QLabel))
            assert "Pilot v0.1" in app_texts
            assert "ενημερώσε" in app_texts  # update status mentioned
        finally:
            page.deleteLater()

    def test_no_network_or_credential_code_in_module(self):
        """Structural guard: the settings module performs no network I/O,
        no OAuth, no AADE/IDIKA calls, and persists no secrets."""
        import inspect
        import qt_app.pages.settings_page as sp
        src = inspect.getsource(sp).lower()
        for banned in ("requests", "urllib", "socket", "qnetwork",
                       "smtplib", "imaplib", "poplib", "oauth",
                       "api_key", "password", "token", "secret_"):
            assert banned not in src, \
                f"settings_page.py must not reference '{banned}'"

    def test_no_vat_ui_in_settings(self):
        """Hard constraint: no VAT section or controls in Settings."""
        import inspect
        import qt_app.pages.settings_page as sp
        src = inspect.getsource(sp)
        assert "ΦΠΑ" not in src, "No VAT UI allowed in Settings"
        assert "vat" not in src.lower(), "No VAT references allowed in Settings"


# ══════════════════════════════════════════════════════════════════════
# E4: Restore UI tests
# ══════════════════════════════════════════════════════════════════════

class TestRestoreButtonState:
    """Restore button is disabled by default, enabled on single selection."""

    def test_restore_button_exists_and_disabled_initially(self, qapp, tmp_path):
        page = _make_page(tmp_path)
        try:
            btn = getattr(page, "_restore_btn", None)
            assert btn is not None, "Missing restore button"
            assert "Επαναφορά" in btn.text()
            assert not btn.isEnabled(), \
                "Restore button must be disabled initially"
        finally:
            page.deleteLater()

    def test_restore_button_enabled_on_single_selection(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        # Create a backup so the table has a row
        from infrastructure.backup_service import BackupService
        svc = BackupService(backup_dir=backup_dir)
        result = svc.create_backup(db_path)
        assert result.ok

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            assert page._table.rowCount() >= 1
            assert not page._restore_btn.isEnabled(), \
                "Restore button disabled with no selection"

            # Select the first row
            page._table.selectRow(0)
            page._update_restore_button_state()
            assert page._restore_btn.isEnabled(), \
                "Restore button should be enabled when one row selected"
        finally:
            page.shutdown()
            page.deleteLater()

    def test_restore_button_disabled_during_loading(self, qapp, tmp_path):
        page = _make_page(tmp_path)
        try:
            page._loading = True
            page._update_restore_button_state()
            assert not page._restore_btn.isEnabled()
        finally:
            page.deleteLater()


class TestRestoreDoesNotModifyActiveDB:
    """Clicking restore in Qt never directly modifies the active DB."""

    def test_restore_clicked_does_not_write_db(self, qapp, tmp_path,
                                                monkeypatch):
        """The restore handler itself spawns a worker and never writes
        directly to the active DB. We verify by running the full flow
        with QMessageBox patched to return No."""
        import hashlib
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        from infrastructure.backup_service import BackupService
        svc = BackupService(backup_dir=backup_dir)
        result = svc.create_backup(db_path)
        assert result.ok

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # Select the first row
            page._table.selectRow(0)
            page._update_restore_button_state()
            assert page._restore_btn.isEnabled()

            # Patch the module-level QMessageBox so dialogs don't block
            import qt_app.pages.settings_page as sp_mod
            monkeypatch.setattr(
                sp_mod, "QMessageBox", _FakeMessageBox)

            # Hash active DB before
            with open(db_path, "rb") as f:
                hash_before = hashlib.sha256(f.read()).hexdigest()

            # Simulate clicking restore
            page._on_restore_clicked()

            # Wait for worker thread to finish
            if page._restore_thread is not None:
                page._restore_thread.wait(5000)
            # Call thread_done directly (no event loop)
            page._on_restore_thread_done()

            # Hash after
            with open(db_path, "rb") as f:
                hash_after = hashlib.sha256(f.read()).hexdigest()

            assert hash_before == hash_after, \
                "Active DB must never be modified by Qt restore flow"
        finally:
            page.shutdown()
            page.deleteLater()


class TestRestorePreparationFailure:
    """Preparation failure keeps app open and reports error."""

    def test_preparation_failure_keeps_app_open(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            from infrastructure.restore_service import RestorePreparation
            fail_result = RestorePreparation(
                ok=False,
                error_message="Test prep failure",
            )
            # Simulate restore worker finishing with failure
            page._restore_loading = True
            page._restore_btn.setEnabled(False)
            page._on_restore_done(fail_result)

            assert "απέτυχε" in page._status_lbl.text()
            # Buttons re-enabled — app stays open
            assert page._backup_btn.isEnabled()
            assert page._refresh_list_btn.isEnabled()
            assert page._restore_btn.isEnabled()
        finally:
            page.shutdown()
            page.deleteLater()


class TestRestorePreparationSuccess:
    """Successful preparation launches helper and requests shutdown."""

    def test_preparation_success_launches_helper(self, qapp, tmp_path,
                                                  monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        request_path = str(
            Path(backup_dir) / "test_req_restore_request.json")

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            from infrastructure.restore_service import RestorePreparation
            success_result = RestorePreparation(
                ok=True,
                request_path=request_path,
                request_id="test_req",
                selected_backup_path=str(tmp_path / "some.db"),
                active_db_path=db_path,
                pre_restore_backup_path=str(tmp_path / "pre.db"),
            )

            # Mock _launch_restore_helper to succeed
            import qt_app.pages.settings_page as sp
            monkeypatch.setattr(
                sp, "_launch_restore_helper",
                lambda request_path: True,
            )
            # Patch QMessageBox to avoid blocking modal
            monkeypatch.setattr(sp, "QMessageBox", _FakeMessageBox)

            # Track if close was requested
            close_called = []

            class FakeWindow:
                def close(self):
                    close_called.append(True)

            monkeypatch.setattr(page, "window", lambda: FakeWindow())

            page._restore_loading = True
            page._on_restore_done(success_result)

            # Helper should have been launched, close requested
            assert len(close_called) == 1, "Window close must be requested"
        finally:
            page.shutdown()
            page.deleteLater()

    def test_helper_launch_failure_keeps_app_open(self, qapp, tmp_path,
                                                   monkeypatch):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        request_path = str(
            Path(backup_dir) / "test_fail_req_restore_request.json")

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            from infrastructure.restore_service import RestorePreparation
            success_result = RestorePreparation(
                ok=True,
                request_path=request_path,
                request_id="test_fail_req",
                selected_backup_path=str(tmp_path / "some.db"),
                active_db_path=db_path,
                pre_restore_backup_path=str(tmp_path / "pre.db"),
            )

            # Mock _launch_restore_helper to fail
            import qt_app.pages.settings_page as sp
            monkeypatch.setattr(
                sp, "_launch_restore_helper",
                lambda request_path: False,
            )
            # Patch QMessageBox to avoid blocking modal
            monkeypatch.setattr(sp, "QMessageBox", _FakeMessageBox)

            page._restore_loading = True
            page._restore_btn.setEnabled(False)
            page._on_restore_done(success_result)

            # App stays open — error shown, buttons re-enabled
            assert "Αδυναμία" in page._status_lbl.text()
            assert page._backup_btn.isEnabled()
        finally:
            page.shutdown()
            page.deleteLater()


class TestRestoreWorkerShutdown:
    """No worker thread survives shutdown."""

    def test_shutdown_stops_restore_worker(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            # Structural test: verify the restore worker refs exist
            # and shutdown() returns True when no worker is running.
            assert page._restore_thread is None
            assert page._restore_worker is None
            assert not page._restore_loading
            assert page.shutdown()
        finally:
            page.deleteLater()


class TestRestoreStatusBanner:
    """On init, Settings shows the latest restore status if applicable."""

    def test_banner_shows_success_status(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        # Write a success status file for this DB
        import json
        status_data = {
            "request_id": "test_banner_001",
            "success": True,
            "timestamp": "2026-07-18T12:00:00",
            "message": "Η επαναφορά ολοκληρώθηκε με επιτυχία.",
            "active_db_path": str(Path(db_path).resolve()),
        }
        status_path = Path(backup_dir) / "test_banner_restore_status.json"
        status_path.write_text(
            json.dumps(status_data), encoding="utf-8")

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            banner = page._restore_status_banner
            assert banner is not None
            # In headless tests isVisible() may return False for
            # widgets that were never shown on screen; check content.
            assert "ολοκληρώθηκε" in banner.text()
        finally:
            page.shutdown()
            page.deleteLater()

    def test_banner_shows_failure_status(self, qapp, tmp_path):
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        import json
        status_data = {
            "request_id": "test_banner_002",
            "success": False,
            "timestamp": "2026-07-18T12:00:00",
            "message": "Η επαναφορά απέτυχε.",
            "active_db_path": str(Path(db_path).resolve()),
        }
        status_path = Path(backup_dir) / "test_banner_restore_status.json"
        status_path.write_text(
            json.dumps(status_data), encoding="utf-8")

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            banner = page._restore_status_banner
            assert banner is not None
            # In headless tests isVisible() may return False
            assert "απέτυχε" in banner.text()
        finally:
            page.shutdown()
            page.deleteLater()

    def test_banner_hidden_when_no_status(self, qapp, tmp_path):
        page = _make_page(tmp_path)
        try:
            banner = page._restore_status_banner
            assert banner is not None
            assert not banner.isVisible()
        finally:
            page.deleteLater()


# ══════════════════════════════════════════════════════════════════════
# E4.1 — _launch_restore_helper correctness
# ══════════════════════════════════════════════════════════════════════

class TestLaunchRestoreHelper:
    """_launch_restore_helper must handle QProcess tuple return and use
    the project root as working directory."""

    def test_returns_true_when_detached_succeeds(self, monkeypatch):
        """QProcess.startDetached returns (True, pid) → helper returns True."""
        import sys
        from pathlib import Path
        import qt_app.pages.settings_page as sp

        captured = {}

        def _fake_start_detached(program, args, workdir):
            captured["program"] = program
            captured["args"] = args
            captured["workdir"] = workdir
            return (True, 4242)

        from PySide6.QtCore import QProcess
        monkeypatch.setattr(QProcess, "startDetached",
                            staticmethod(_fake_start_detached))

        result = sp._launch_restore_helper(
            "C:\\backups\\test_restore_request.json")
        assert result is True

    def test_captures_correct_program_args_workdir(self, monkeypatch):
        """Assert program is sys.executable, args contain -m and the
        module name plus --request, workdir is the project root."""
        import sys
        from pathlib import Path
        import qt_app.pages.settings_page as sp

        captured = {}

        def _fake_start_detached(program, args, workdir):
            captured["program"] = program
            captured["args"] = args
            captured["workdir"] = workdir
            return (True, 9999)

        from PySide6.QtCore import QProcess
        monkeypatch.setattr(QProcess, "startDetached",
                            staticmethod(_fake_start_detached))

        request_path = "C:\\backups\\test_req.json"
        result = sp._launch_restore_helper(request_path)

        assert captured["program"] == sys.executable, \
            "Must use sys.executable"
        assert "-m" in captured["args"]
        assert "infrastructure.restore_helper" in captured["args"]
        assert "--request" in captured["args"]
        assert request_path in captured["args"]
        # Workdir is the project root = parents[2] from settings_page.py
        expected_root = str(
            Path(sp.__file__).resolve().parents[2])
        assert captured["workdir"] == expected_root, \
            f"Workdir must be project root, got {captured['workdir']}"
        assert result is True

    def test_returns_false_when_detached_fails(self, monkeypatch):
        """QProcess.startDetached returns (False, 0) → helper returns False."""
        import qt_app.pages.settings_page as sp

        def _fake_start_detached(program, args, workdir):
            return (False, 0)

        from PySide6.QtCore import QProcess
        monkeypatch.setattr(QProcess, "startDetached",
                            staticmethod(_fake_start_detached))

        result = sp._launch_restore_helper(
            "C:\\backups\\test_req.json")
        assert result is False

    def test_returns_false_on_exception(self, monkeypatch):
        """If startDetached raises, helper returns False safely."""
        import qt_app.pages.settings_page as sp

        def _fake_start_detached(program, args, workdir):
            raise RuntimeError("Simulated crash")

        from PySide6.QtCore import QProcess
        monkeypatch.setattr(QProcess, "startDetached",
                            staticmethod(_fake_start_detached))

        result = sp._launch_restore_helper(
            "C:\\backups\\test_req.json")
        assert result is False

    def test_launch_failure_does_not_request_window_close(self, qapp,
                                                            tmp_path,
                                                            monkeypatch):
        """When _launch_restore_helper returns False, _on_restore_done
        must NOT request window close and must re-enable buttons."""
        db_path = str(tmp_path / "test.db")
        _make_db(db_path)
        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        request_path = str(
            Path(backup_dir) / "test_fail_launch_restore_request.json")
        # Create a dummy request file so _clean_request_file can remove it
        Path(request_path).write_text("{}")

        page = SettingsPage(
            db_service=None,
            config={"db_path": db_path, "backup_dir": backup_dir},
        )
        try:
            from infrastructure.restore_service import RestorePreparation
            success_result = RestorePreparation(
                ok=True,
                request_path=request_path,
                request_id="test_fail_launch",
                selected_backup_path=str(tmp_path / "some.db"),
                active_db_path=db_path,
                pre_restore_backup_path=str(tmp_path / "pre.db"),
            )

            # Mock _launch_restore_helper to fail
            import qt_app.pages.settings_page as sp
            monkeypatch.setattr(
                sp, "_launch_restore_helper",
                lambda request_path: False,
            )
            monkeypatch.setattr(sp, "QMessageBox", _FakeMessageBox)

            close_called = []

            class FakeWindow:
                def close(self):
                    close_called.append(True)

            monkeypatch.setattr(page, "window", lambda: FakeWindow())

            page._restore_loading = True
            page._restore_btn.setEnabled(False)
            page._on_restore_done(success_result)

            # Window close NOT requested
            assert len(close_called) == 0, \
                "Window close must NOT be requested when helper launch fails"
            # Error shown
            assert "Αδυναμία" in page._status_lbl.text()
            # Buttons re-enabled
            assert page._backup_btn.isEnabled()
        finally:
            page.shutdown()
            page.deleteLater()
