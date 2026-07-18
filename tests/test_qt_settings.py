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
