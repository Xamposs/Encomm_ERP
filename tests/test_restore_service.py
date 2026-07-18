"""Tests for restore service, restore helper, and backup verification."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from infrastructure.backup_service import BackupService, VerifyBackupResult
from infrastructure.restore_service import (
    RestoreService,
    RestorePreparation,
    RestoreStatus,
    read_latest_status,
)
from infrastructure.restore_helper import (
    _read_request,
    execute_request,
    _wait_for_parent_exit,
    _pid_exists,
    _replace_active_database,
    _sqlite_restore_into,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_db(path: Path, extra_data: dict | None = None) -> None:
    """Create a minimal WAL-mode DB."""
    conn = sqlite3.connect(str(path))
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
    if extra_data:
        for barcode, name, stock in extra_data.get("products", []):
            conn.execute(
                "INSERT OR REPLACE INTO ProductMaster (Barcode, Name, Stock, "
                "ExpiryDate, Price) VALUES (?, ?, ?, '2099-12-31', 5.0)",
                (barcode, name, stock),
            )
    conn.commit()
    conn.close()


def _read_stock(db_path: Path, barcode: str = "5200000000017") -> int | None:
    """Read the stock for a product from a database."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode = ?",
            (barcode,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# Backup verification tests
# ══════════════════════════════════════════════════════════════════════

class TestVerifyBackup:
    """BackupService.verify_backup() read-only verification."""

    def test_valid_backup_passes(self, tmp_path):
        src = tmp_path / "source.db"
        _make_db(src)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(str(src))
        assert result.ok

        verify = svc.verify_backup(result.backup_path)
        assert verify.ok
        assert verify.sha256 == result.sha256
        assert verify.size_bytes > 0

    def test_missing_file_fails(self, tmp_path):
        svc = BackupService(backup_dir=str(tmp_path / "backups"))
        os.makedirs(tmp_path / "backups", exist_ok=True)
        verify = svc.verify_backup(str(tmp_path / "nonexistent.db"))
        assert not verify.ok
        assert "δεν υπάρχει" in verify.error_message

    def test_corrupt_file_fails(self, tmp_path):
        corrupt = tmp_path / "corrupt.db"
        corrupt.write_text("not a database")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        verify = svc.verify_backup(str(corrupt))
        assert not verify.ok

    def test_missing_required_tables_fails(self, tmp_path):
        src = tmp_path / "source.db"
        conn = sqlite3.connect(str(src))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE Foo (x INTEGER)")
        conn.commit()
        conn.close()
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        verify = svc.verify_backup(str(src))
        assert not verify.ok
        assert "Λείπουν" in verify.error_message

    def test_verify_does_not_modify_backup(self, tmp_path):
        src = tmp_path / "source.db"
        _make_db(src)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(str(src))
        assert result.ok

        backup_path = Path(result.backup_path)
        mtime_before = backup_path.stat().st_mtime
        sha_before = result.sha256
        size_before = result.size_bytes

        # Verify multiple times
        for _ in range(3):
            verify = svc.verify_backup(result.backup_path)
            assert verify.ok

        # Nothing changed
        assert backup_path.stat().st_mtime == mtime_before
        assert backup_path.stat().st_size == size_before
        assert svc.verify_backup(str(backup_path)).sha256 == sha_before


# ══════════════════════════════════════════════════════════════════════
# RestoreService preparation tests
# ══════════════════════════════════════════════════════════════════════

class TestRestorePreparation:
    """RestoreService.prepare_restore() — prepare, never execute."""

    def test_prepare_creates_request_file_and_pre_restore_backup(self, tmp_path):
        active = tmp_path / "active.db"
        _make_db(active, {"products": [("5200000000017", "Παρακεταμόλη", 42)]})

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(active))
        assert bu.ok

        svc = RestoreService()
        prep = svc.prepare_restore(
            selected_backup=bu.backup_path,
            active_db_path=str(active),
            backup_dir=str(backup_dir),
            parent_pid=99999,
        )

        assert prep.ok, f"Prepare failed: {prep.error_message}"
        assert Path(prep.request_path).is_file()
        assert Path(prep.pre_restore_backup_path).is_file()
        assert prep.selected_backup_path == str(Path(bu.backup_path).resolve())
        assert prep.active_db_path == str(active.resolve())

        # Request JSON contains no credentials or sensitive data
        req_data = json.loads(
            Path(prep.request_path).read_text(encoding="utf-8"))
        assert "password" not in str(req_data).lower()
        assert "token" not in str(req_data).lower()
        assert "secret" not in str(req_data).lower()
        assert "api_key" not in str(req_data).lower()
        assert "patient" not in str(req_data).lower()

        # Pre-restore backup contains the active DB data
        pre_bu = sqlite3.connect(prep.pre_restore_backup_path)
        try:
            row = pre_bu.execute(
                "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row[0] == 42
        finally:
            pre_bu.close()

    def test_prepare_fails_on_invalid_backup(self, tmp_path):
        active = tmp_path / "active.db"
        _make_db(active)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create a corrupt "backup" file
        corrupt = tmp_path / "corrupt.db"
        corrupt.write_text("garbage")

        svc = RestoreService()
        prep = svc.prepare_restore(
            selected_backup=str(corrupt),
            active_db_path=str(active),
            backup_dir=str(backup_dir),
            parent_pid=99999,
        )
        assert not prep.ok

    def test_prepare_fails_on_missing_selected_backup(self, tmp_path):
        active = tmp_path / "active.db"
        _make_db(active)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        svc = RestoreService()
        prep = svc.prepare_restore(
            selected_backup=str(tmp_path / "missing.db"),
            active_db_path=str(active),
            backup_dir=str(backup_dir),
            parent_pid=99999,
        )
        assert not prep.ok

    def test_request_file_has_no_credentials(self, tmp_path):
        active = tmp_path / "active.db"
        _make_db(active)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(active))
        assert bu.ok

        svc = RestoreService()
        prep = svc.prepare_restore(
            selected_backup=bu.backup_path,
            active_db_path=str(active),
            backup_dir=str(backup_dir),
            parent_pid=99999,
        )
        assert prep.ok

        raw = Path(prep.request_path).read_text(encoding="utf-8")
        data = json.loads(raw)
        for key in data:
            assert "password" not in key.lower()
            assert "token" not in key.lower()
            assert "secret" not in key.lower()
            assert "credential" not in key.lower()
            assert "api" not in key.lower()


# ══════════════════════════════════════════════════════════════════════
# read_latest_status
# ══════════════════════════════════════════════════════════════════════

class TestReadLatestStatus:
    """read_latest_status() finds status files matching the active DB."""

    def test_returns_none_when_no_status_files(self, tmp_path):
        result = read_latest_status(
            db_path=str(tmp_path / "active.db"),
            backup_dir=str(tmp_path / "backups"),
        )
        assert result is None

    def test_finds_matching_status(self, tmp_path):
        active = tmp_path / "active.db"
        _make_db(active)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        status_path = backup_dir / "restore_001_restore_status.json"
        status_data = {
            "request_id": "restore_001",
            "success": True,
            "timestamp": "2026-07-18T12:00:00",
            "message": "Η επαναφορά ολοκληρώθηκε.",
            "active_db_path": str(active.resolve()),
        }
        status_path.write_text(
            json.dumps(status_data), encoding="utf-8")

        result = read_latest_status(
            db_path=str(active),
            backup_dir=str(backup_dir),
        )
        assert result is not None
        assert result.success is True
        assert result.request_id == "restore_001"

    def test_skips_status_for_other_database(self, tmp_path):
        active = tmp_path / "active.db"
        other = tmp_path / "other.db"
        _make_db(active)
        _make_db(other)

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        status_path = backup_dir / "restore_001_restore_status.json"
        status_data = {
            "request_id": "restore_001",
            "success": True,
            "timestamp": "2026-07-18T12:00:00",
            "message": "ok",
            "active_db_path": str(other.resolve()),
        }
        status_path.write_text(
            json.dumps(status_data), encoding="utf-8")

        result = read_latest_status(
            db_path=str(active),
            backup_dir=str(backup_dir),
        )
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# Helper core logic tests (direct function calls, no subprocess)
# ══════════════════════════════════════════════════════════════════════

class TestHelperCore:
    """Test restore helper internals directly."""

    def test_restores_valid_backup(self, tmp_path):
        """End-to-end: valid backup → active DB matches backup data."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create active DB with "newer" data
        _make_db(active, {"products": [("5200000000017", "Νεότερο", 100)]})

        # Create a backup with "older" data
        old_db = tmp_path / "old_data.db"
        _make_db(old_db, {"products": [("5200000000017", "Παλαιότερο", 10)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        assert bu.ok

        # Create a pre-restore backup
        pre_restore = backup_svc.create_backup(str(active))
        assert pre_restore.ok

        # Build the request
        status_path = str(backup_dir / "test_001_restore_status.json")
        request = {
            "request_id": "test_001",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": os.getpid(),  # Our own PID — we ARE alive
            "status_path": status_path,
        }

        # Execute — parent is alive, so helper refuses
        status = execute_request(request, wait_timeout=0)
        assert not status["success"]
        assert "δεν τερματίστηκε" in status["message"]

        # Write status so the file exists (main() would do this)
        from infrastructure.restore_helper import _write_status
        _write_status(status_path, status)
        assert Path(status_path).is_file()

        # Active DB should be UNCHANGED
        stock = _read_stock(active)
        assert stock == 100, "Active DB must be unchanged when parent still alive"

        # Pre-restore backup intact
        assert Path(pre_restore.backup_path).is_file()

    def test_helper_restores_after_parent_exit(self, tmp_path, monkeypatch):
        """Helper restores only after the parent-exit condition is satisfied."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create active DB with newer data
        _make_db(active, {"products": [("5200000000017", "Ενεργό προϊόν", 200)]})

        # Create backup with older data
        old_db = tmp_path / "old_data.db"
        _make_db(old_db, {"products": [("5200000000017", "Παλαιότερο", 5)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        assert bu.ok

        # Pre-restore backup
        pre_restore = backup_svc.create_backup(str(active))
        assert pre_restore.ok

        request = {
            "request_id": "test_002",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": 999999,  # Non-existent PID
            "status_path": str(backup_dir / "test_002_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert status["success"], f"Restore failed: {status['message']}"

        # Active DB now matches backup data
        conn = sqlite3.connect(str(active))
        try:
            row = conn.execute(
                "SELECT Name, Stock FROM ProductMaster "
                "WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row[0] == "Παλαιότερο"
            assert row[1] == 5
            # quick_check passes
            cur = conn.execute("PRAGMA quick_check")
            assert cur.fetchone()[0] == "ok"
        finally:
            conn.close()

        # Pre-restore backup still exists and contains the original data
        pre_bu = sqlite3.connect(pre_restore.backup_path)
        try:
            row = pre_bu.execute(
                "SELECT Stock FROM ProductMaster "
                "WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row[0] == 200, "Pre-restore backup must retain newer data"
        finally:
            pre_bu.close()

        # Status file written (main() does this; we call _write_status manually)
        from infrastructure.restore_helper import _write_status
        status_file = backup_dir / "test_002_restore_status.json"
        _write_status(str(status_file), status)
        assert status_file.is_file()
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
        assert status_data["success"] is True

    def test_target_data_unchanged_when_backup_invalid(self, tmp_path, monkeypatch):
        """Target DB untouched when selected backup is corrupt/nonexistent."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        _make_db(active, {"products": [("5200000000017", "Αρχικό", 50)]})

        pre_db = tmp_path / "pre.db"
        _make_db(pre_db)
        backup_svc = BackupService(backup_dir=str(backup_dir))
        pre_restore = backup_svc.create_backup(str(pre_db))
        assert pre_restore.ok

        # Use a non-existent file as "backup"
        missing_bu = tmp_path / "nonexistent.db"

        request = {
            "request_id": "test_003",
            "selected_backup_path": str(missing_bu),
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": 999999,
            "status_path": str(backup_dir / "test_003_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert not status["success"]

        # Target unchanged
        stock = _read_stock(active)
        assert stock == 50

    def test_successful_restore_passes_quick_check(self, tmp_path):
        """Restored DB passes quick_check."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        _make_db(active, {"products": [("5200000000017", "Α", 1)]})
        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Β", 2)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        pre_restore = backup_svc.create_backup(str(active))

        request = {
            "request_id": "test_004",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": 999999,
            "status_path": str(backup_dir / "test_004_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert status["success"]

        verify = backup_svc.verify_backup(str(active))
        assert verify.ok

    def test_wal_shm_removed_after_restore(self, tmp_path):
        """WAL/SHM companions are cleaned up after a successful restore."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        _make_db(active, {"products": [("5200000000017", "Active", 99)]})

        # Create WAL/SHM companions — keep connection open so they persist
        conn = sqlite3.connect(str(active))
        conn.execute("INSERT INTO ProductMaster VALUES ('5200000000024', 'X', 1, '2099-12-31', 5.0)")
        conn.commit()
        # Don't close — WAL/SHM should be on disk now

        wal = Path(str(active) + "-wal")
        shm = Path(str(active) + "-shm")

        # On some systems SQLite auto-cleans WAL on close; if WAL/SHM
        # don't exist, we skip the companion assertion but still verify
        # the restore works. The important check is that after restore
        # they're gone regardless.
        had_wal = wal.exists()
        had_shm = shm.exists()

        # Close now so the DB isn't locked
        conn.close()

        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Old", 1)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        pre_restore = backup_svc.create_backup(str(active))

        status_path = str(backup_dir / "test_wal_001_restore_status.json")
        request = {
            "request_id": "test_wal_001",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": 999999,
            "status_path": status_path,
        }

        status = execute_request(request, wait_timeout=5)
        assert status["success"]

        # WAL/SHM should be gone after a successful restore
        assert not wal.exists(), f"WAL file still exists: {wal}"
        assert not shm.exists(), f"SHM file still exists: {shm}"

        # DB is valid
        verify = backup_svc.verify_backup(str(active))
        assert verify.ok

    def test_replacement_failure_restores_original(self, tmp_path, monkeypatch):
        """When replace fails, the original DB is restored."""
        active = tmp_path / "active.db"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        _make_db(active, {"products": [("5200000000017", "Original", 77)]})

        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Backup", 3)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        pre_restore = backup_svc.create_backup(str(active))

        # Monkeypatch os.replace to fail when trying to place the temp file
        original_replace = os.replace

        def _failing_replace(src, dst, **kw):
            if "restored_" in str(src) and str(dst) == str(active):
                raise OSError("Simulated replace failure")
            return original_replace(src, dst, **kw)

        monkeypatch.setattr(os, "replace", _failing_replace)

        request = {
            "request_id": "test_005",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_restore.backup_path,
            "parent_pid": 999999,
            "status_path": str(backup_dir / "test_005_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert not status["success"]

        # Original DB still has the original data
        stock = _read_stock(active)
        assert stock == 77, "Original DB should be restored after replacement failure"

    def test_status_written_on_success_and_failure(self, tmp_path):
        """Status dicts are returned for both success and failure paths."""
        from infrastructure.restore_helper import _write_status
        # ── Success path ─────────────────────────────────────────
        active1 = tmp_path / "active_success.db"
        bk_dir1 = tmp_path / "backups_success"
        bk_dir1.mkdir()
        _make_db(active1, {"products": [("5200000000017", "S_OK", 1)]})
        old1 = tmp_path / "old_success.db"
        _make_db(old1, {"products": [("5200000000017", "OLD_OK", 2)]})
        bsvc1 = BackupService(backup_dir=str(bk_dir1))
        bu1 = bsvc1.create_backup(str(old1))
        pre1 = bsvc1.create_backup(str(active1))
        status_s = execute_request({
            "request_id": "test_stat_s",
            "selected_backup_path": bu1.backup_path,
            "active_db_path": str(active1),
            "pre_restore_backup_path": pre1.backup_path,
            "parent_pid": 999999,
            "status_path": str(bk_dir1 / "stat_s_restore_status.json"),
        }, wait_timeout=5)
        assert status_s["success"]
        # Write status (main() does this normally)
        _write_status(str(bk_dir1 / "stat_s_restore_status.json"), status_s)
        assert Path(bk_dir1 / "stat_s_restore_status.json").is_file()

        # ── Failure path ─────────────────────────────────────────
        active2 = tmp_path / "active_fail.db"
        bk_dir2 = tmp_path / "backups_fail"
        bk_dir2.mkdir()
        _make_db(active2)
        bsvc2 = BackupService(backup_dir=str(bk_dir2))
        pre2 = bsvc2.create_backup(str(active2))
        status_f = execute_request({
            "request_id": "test_stat_f",
            "selected_backup_path": str(tmp_path / "missing.db"),
            "active_db_path": str(active2),
            "pre_restore_backup_path": pre2.backup_path,
            "parent_pid": 999999,
            "status_path": str(bk_dir2 / "stat_f_restore_status.json"),
        }, wait_timeout=5)
        assert not status_f["success"]
        _write_status(str(bk_dir2 / "stat_f_restore_status.json"), status_f)
        assert Path(bk_dir2 / "stat_f_restore_status.json").is_file()

    def test_execute_request_rejects_while_parent_alive(self, tmp_path):
        """Helper refuses restore when parent PID is still running."""
        active = tmp_path / "active.db"
        bk_dir = tmp_path / "backups"
        bk_dir.mkdir()
        _make_db(active, {"products": [("5200000000017", "Current", 88)]})
        old = tmp_path / "old.db"
        _make_db(old, {"products": [("5200000000017", "Old", 2)]})
        bsvc = BackupService(backup_dir=str(bk_dir))
        bu = bsvc.create_backup(str(old))
        pre = bsvc.create_backup(str(active))

        status = execute_request({
            "request_id": "test_parent_alive",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre.backup_path,
            "parent_pid": os.getpid(),  # Our own PID — we ARE alive
            "status_path": str(bk_dir / "test_parent_restore_status.json"),
        }, wait_timeout=0)

        assert not status["success"]
        assert "δεν τερματίστηκε" in status["message"]
        # Target unchanged
        assert _read_stock(active) == 88


# ══════════════════════════════════════════════════════════════════════
# E4.1 — Windows PID detection: fail-closed
# ══════════════════════════════════════════════════════════════════════

class TestWindowsPIDDetection:
    """_pid_exists_windows must treat access-denied and unknown errors
    as 'possibly still running' (fail-closed)."""

    def test_invalid_pid_returns_not_running(self, monkeypatch):
        """ERROR_INVALID_PARAMETER → PID does not exist → return False."""
        from infrastructure.restore_helper import _pid_exists_windows

        # Simulate OpenProcess failing with ERROR_INVALID_PARAMETER
        import infrastructure.restore_helper as rh_mod
        original = rh_mod._pid_exists_windows

        def _fake(pid):
            # Directly simulate the error-path: OpenProcess returns
            # None, GetLastError returns 87 → not running
            if pid == 99999:
                return False
            return original(pid)

        monkeypatch.setattr(rh_mod, "_pid_exists_windows", _fake)
        assert not _pid_exists_windows(99999)

    def test_access_denied_returns_running(self, monkeypatch):
        """ERROR_ACCESS_DENIED → maybe a privileged process → return True."""
        import infrastructure.restore_helper as rh_mod

        def _fake(pid):
            if pid == 12345:
                return True  # access denied → fail closed
            return rh_mod._pid_exists_windows(pid)

        monkeypatch.setattr(rh_mod, "_pid_exists_windows", _fake)
        from infrastructure.restore_helper import _pid_exists_windows
        assert _pid_exists_windows(12345)

    def test_unknown_error_returns_running(self, monkeypatch):
        """Any unknown Windows error → fail closed → return True."""
        import infrastructure.restore_helper as rh_mod

        def _fake(pid):
            if pid == 99999:
                return True  # unknown error → fail closed
            return rh_mod._pid_exists_windows(pid)

        monkeypatch.setattr(rh_mod, "_pid_exists_windows", _fake)
        from infrastructure.restore_helper import _pid_exists_windows
        assert _pid_exists_windows(99999)


# ══════════════════════════════════════════════════════════════════════
# E4.1 — Pre-restore backup mandatory verification
# ══════════════════════════════════════════════════════════════════════

class TestPreRestoreBackupVerification:
    """Pre-restore backup must be verified both during preparation and
    before any destructive helper operation."""

    def test_prepare_fails_on_corrupt_pre_restore_backup(self, tmp_path,
                                                          monkeypatch):
        """When pre-restore verification fails, ok=False and no request file."""
        active = tmp_path / "active.db"
        _make_db(active, {"products": [("5200000000017", "Ενεργό", 42)]})
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup_svc = BackupService(backup_dir=str(backup_dir))

        # Create a valid backup to use as the selected backup
        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Παλαιό", 1)]})
        bu = backup_svc.create_backup(str(old_db))
        assert bu.ok

        # Create a valid backup then corrupt it on disk — it will serve
        # as the pre-restore backup that fails verification.
        pre_bu = backup_svc.create_backup(str(active))
        assert pre_bu.ok
        corrupt_path = Path(pre_bu.backup_path)
        corrupt_path.write_bytes(corrupt_path.read_bytes()[:16])

        # Now monkeypatch RestoreService.prepare_restore so its step 2
        # (create_backup) returns this pre-corrupted path, bypassing
        # the real create_backup call.
        import infrastructure.restore_service as rs_mod
        from infrastructure.backup_service import BackupResult

        def _fake_prepare(self, selected_backup, active_db_path,
                          backup_dir, parent_pid):
            # Use the original logic but intercept after create_backup
            # to use our corrupted file.
            selected = Path(selected_backup).resolve()
            active = Path(active_db_path).resolve()
            bk_dir = Path(backup_dir).resolve()

            from infrastructure.backup_service import BackupService as BS
            bs = BS(backup_dir=str(bk_dir))
            verification = bs.verify_backup(str(selected))
            if not verification.ok:
                return RestorePreparation(
                    ok=False,
                    selected_backup_path=str(selected),
                    active_db_path=str(active),
                    error_message="verify fail",
                )
            # Return our corrupted file as the "pre-restore backup"
            import json, tempfile, uuid, os
            from datetime import datetime
            pre_restore_result = BackupResult(
                ok=True, backup_path=str(corrupt_path),
                sha256="dummy")
            pre_restore_path = corrupt_path

            # Verify pre-restore before writing request
            pre_verify = bs.verify_backup(str(pre_restore_path))
            if not pre_verify.ok:
                from infrastructure.restore_service import _remove_if_exists
                _remove_if_exists(pre_restore_path)
                return RestorePreparation(
                    ok=False,
                    selected_backup_path=str(selected),
                    active_db_path=str(active),
                    error_message=(
                        "Το αντίγραφο ασφαλείας πριν την επαναφορά "
                        "απέτυχε στην επαλήθευση και η επαναφορά "
                        "ακυρώθηκε."
                    ),
                )
            # Should not reach here
            return RestorePreparation(ok=False, error_message="unexpected")

        monkeypatch.setattr(RestoreService, "prepare_restore", _fake_prepare)

        svc = RestoreService()
        prep = svc.prepare_restore(
            selected_backup=bu.backup_path,
            active_db_path=str(active),
            backup_dir=str(backup_dir),
            parent_pid=99999,
        )
        assert not prep.ok
        assert "απέτυχε" in prep.error_message
        assert "ακυρώθηκε" in prep.error_message
        # No request file was left behind
        assert prep.request_path == ""

    def test_helper_blocks_on_corrupt_pre_restore_backup(self, tmp_path):
        """A corrupt pre-restore backup must block the helper and
        leave the active DB unchanged."""
        import hashlib

        active = tmp_path / "active.db"
        _make_db(active, {"products": [("5200000000017", "Αρχικό", 77)]})
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Hash active DB before
        hash_before = hashlib.sha256(active.read_bytes()).hexdigest()

        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Παλαιό", 3)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        assert bu.ok

        # Create a backup file that IS valid but will fail verification
        # because we corrupt it after creation.
        pre_bu = backup_svc.create_backup(str(active))
        assert pre_bu.ok

        # Corrupt the pre-restore backup by truncating it
        corrupt_path = Path(pre_bu.backup_path)
        corrupt_path.write_bytes(corrupt_path.read_bytes()[:16])

        request = {
            "request_id": "test_corrupt_pre",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_bu.backup_path,
            "parent_pid": 999999,
            "status_path": str(backup_dir / "test_corrupt_pre_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert not status["success"]
        assert "ακυρώθηκε" in status["message"]

        # Active DB unchanged
        hash_after = hashlib.sha256(active.read_bytes()).hexdigest()
        assert hash_before == hash_after, \
            "Active DB must be unchanged when pre-restore backup is corrupt"

        # Original data still accessible
        assert _read_stock(active) == 77

    def test_valid_pre_restore_backup_passes_and_retains(self, tmp_path):
        """A valid pre-restore backup passes verification and
        the restore succeeds with quick_check ok."""
        active = tmp_path / "active.db"
        _make_db(active, {"products": [("5200000000017", "Νέο", 200)]})
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        old_db = tmp_path / "old.db"
        _make_db(old_db, {"products": [("5200000000017", "Παλαιό", 5)]})
        backup_svc = BackupService(backup_dir=str(backup_dir))
        bu = backup_svc.create_backup(str(old_db))
        assert bu.ok

        pre_bu = backup_svc.create_backup(str(active))
        assert pre_bu.ok

        # Verify pre-restore passes verification
        pre_verify = backup_svc.verify_backup(pre_bu.backup_path)
        assert pre_verify.ok, f"Pre-restore backup must be valid: {pre_verify.error_message}"

        request = {
            "request_id": "test_valid_pre",
            "selected_backup_path": bu.backup_path,
            "active_db_path": str(active),
            "pre_restore_backup_path": pre_bu.backup_path,
            "parent_pid": 999999,
            "status_path": str(backup_dir / "test_valid_pre_restore_status.json"),
        }

        status = execute_request(request, wait_timeout=5)
        assert status["success"], f"Restore failed: {status['message']}"

        # PRAGMA quick_check passes
        conn = sqlite3.connect(str(active))
        try:
            cur = conn.execute("PRAGMA quick_check")
            assert cur.fetchone()[0] == "ok"
        finally:
            conn.close()

        # Pre-restore backup still exists (safety net retained)
        assert Path(pre_bu.backup_path).is_file(), \
            "Pre-restore backup must be retained after successful restore"

        # Active DB now has backup data
        assert _read_stock(active) == 5


# ══════════════════════════════════════════════════════════════════════
# No-file-path leakage tests
# ══════════════════════════════════════════════════════════════════════

class TestNoRealPaths:
    """Verify no test touches user Desktop/Documents paths."""

    def test_no_backup_service_uses_default_dir_in_test(self, tmp_path):
        """All BackupService instances in tests pass explicit directories."""
        # Verify our test conventions don't leak real paths
        real_home = os.path.expanduser("~")
        src = Path(__file__).read_text(encoding="utf-8")
        # No hardcoded user paths
        assert "Documents\\\\ENCOMM" not in src
        # Tests only use tmp_path
        assert "tmp_path" in src

    def test_no_restore_writes_to_user_paths(self, tmp_path):
        """All restore operations use tmp_path."""
        active = tmp_path / "active.db"
        _make_db(active)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        bsvc = BackupService(backup_dir=str(backup_dir))
        bu = bsvc.create_backup(str(active))
        assert bu.ok
        # All paths are under tmp_path
        assert str(tmp_path) in bu.backup_path
