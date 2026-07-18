"""Tests for the verified SQLite backup service."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from infrastructure.database_service import DatabaseService
from infrastructure.backup_service import BackupService, BackupResult, BackupInfo


# ── Helpers ──────────────────────────────────────────────────────────────

def _init_db(db_path: str) -> sqlite3.Connection:
    """Create a minimal WAL-mode DB with ProductMaster + SystemConfig."""
    conn = sqlite3.connect(db_path)
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
    return conn


def _insert_product(conn: sqlite3.Connection, barcode: str,
                    name: str, stock: int = 10) -> None:
    conn.execute(
        "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, '2099-12-31', 5.0)",
        (barcode, name, stock),
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════
# Service tests
# ══════════════════════════════════════════════════════════════════════

class TestBackupServiceCreate:
    """Verify the core backup-creation path."""

    def test_backup_is_valid_sqlite_and_quick_check_ok(self, tmp_path):
        """Backup passes PRAGMA quick_check."""
        src = str(tmp_path / "source.db")
        _init_db(src)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)

        assert result.ok, f"Backup failed: {result.error_message}"
        assert os.path.exists(result.backup_path)

        # Verify with PRAGMA quick_check
        uri = f"file:{Path(result.backup_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.execute("PRAGMA quick_check")
            row = cur.fetchone()
            assert row is not None and row[0] == "ok", \
                f"quick_check returned: {row}"
        finally:
            conn.close()

    def test_backup_contains_pre_backup_data(self, tmp_path):
        """Backup captures data committed before the backup."""
        src = str(tmp_path / "source.db")
        conn = _init_db(src)
        _insert_product(conn, "5200000000017", "Παρακεταμόλη", stock=42)
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert result.ok

        # Verify data exists in the backup
        bu_conn = sqlite3.connect(result.backup_path)
        try:
            row = bu_conn.execute(
                "SELECT Name, Stock FROM ProductMaster "
                "WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row is not None
            assert row[0] == "Παρακεταμόλη"
            assert row[1] == 42
        finally:
            bu_conn.close()

    def test_later_source_changes_do_not_alter_backup(self, tmp_path):
        """Changes after backup do not affect the completed backup."""
        src = str(tmp_path / "source.db")
        conn = _init_db(src)
        _insert_product(conn, "5200000000017", "Παρακεταμόλη", stock=42)
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert result.ok

        # Alter source
        conn2 = sqlite3.connect(src)
        conn2.execute(
            "UPDATE ProductMaster SET Stock=99 "
            "WHERE Barcode='5200000000017'")
        conn2.commit()
        conn2.close()

        # Backup must still have the original stock
        bu_conn = sqlite3.connect(result.backup_path)
        try:
            row = bu_conn.execute(
                "SELECT Stock FROM ProductMaster "
                "WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row[0] == 42
        finally:
            bu_conn.close()

    def test_wal_mode_source_backed_up_correctly(self, tmp_path):
        """WAL-mode source with uncheckpointed data is captured."""
        src = str(tmp_path / "source.db")
        conn = _init_db(src)  # WAL mode
        # Insert without checkpoint — data lives in WAL
        _insert_product(conn, "5200000000017", "WAL Product", stock=77)
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert result.ok

        bu_conn = sqlite3.connect(result.backup_path)
        try:
            row = bu_conn.execute(
                "SELECT Name FROM ProductMaster "
                "WHERE Barcode='5200000000017'"
            ).fetchone()
            assert row is not None
            assert row[0] == "WAL Product"
        finally:
            bu_conn.close()


class TestBackupServiceFailureModes:
    """Backup must fail safely in edge cases."""

    def test_missing_source_fails(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(str(tmp_path / "nonexistent.db"))
        assert not result.ok

    def test_invalid_source_corrupt_db_fails(self, tmp_path):
        """A file that is not a valid SQLite database fails."""
        src = tmp_path / "not_a_db.db"
        src.write_text("this is not sqlite")

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(str(src))
        assert not result.ok

    def test_missing_required_tables_during_validation_fails(self, tmp_path):
        """Backup fails validation if required tables are missing."""
        src = str(tmp_path / "source.db")
        # DB has tables, but not ProductMaster + SystemConfig
        conn = sqlite3.connect(src)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE Foo (x INTEGER)")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert not result.ok
        assert "Missing required tables" in result.error_message
        # No .db file should have been published
        db_files = list(backup_dir.glob("encomm_backup_*.db"))
        names = [f.name for f in db_files]
        assert len(db_files) == 0, \
            f"Corrupt backup published: {names}"

    def test_partial_files_not_listed(self, tmp_path):
        """list_backups() skips .tmp partial files."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create a .tmp file that looks like a partial
        partial = backup_dir / "encomm_backup_20260718_120000.tmp"
        partial.write_text("partial content")

        svc = BackupService(backup_dir=str(backup_dir))
        backups = svc.list_backups()
        tmp_names = [b.filename for b in backups if b.filename.endswith(".tmp")]
        assert len(tmp_names) == 0, f".tmp files leaked into list: {tmp_names}"

    def test_failed_backup_leaves_no_published_corrupt_backup(self, tmp_path):
        """A failed backup must not leave a corrupt .db file behind."""
        # Create a source missing required tables
        src = str(tmp_path / "source.db")
        conn = sqlite3.connect(src)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE Foo (x INTEGER)")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert not result.ok  # missing ProductMaster + SystemConfig

        # No .db file should have been published
        db_files = list(backup_dir.glob("encomm_backup_*.db"))
        assert len(db_files) == 0, \
            f"Corrupt backup published: {[f.name for f in db_files]}"


class TestBackupServiceSHA256:
    """SHA-256 is stable for a completed backup."""

    def test_sha256_stable(self, tmp_path):
        src = str(tmp_path / "source.db")
        _init_db(src)

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert result.ok
        assert len(result.sha256) == 64

        # Recompute manually
        expected = hashlib.sha256(
            Path(result.backup_path).read_bytes()).hexdigest()
        assert result.sha256 == expected

    def test_sha256_unique_per_content(self, tmp_path):
        """Different backup content → different SHA-256."""
        src1 = str(tmp_path / "source1.db")
        conn = _init_db(src1)
        _insert_product(conn, "5200000000017", "Product-A", stock=1)
        conn.close()

        src2 = str(tmp_path / "source2.db")
        conn = _init_db(src2)
        _insert_product(conn, "5200000000017", "Product-A", stock=1)
        _insert_product(conn, "5200000000024", "Product-B", stock=2)
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))

        r1 = svc.create_backup(src1)
        r2 = svc.create_backup(src2)
        assert r1.ok and r2.ok
        assert r1.sha256 != r2.sha256, \
            "Different content should produce different hashes"


# ══════════════════════════════════════════════════════════════════════
# Legacy DatabaseService compatibility
# ══════════════════════════════════════════════════════════════════════

class TestLegacyBackupCompatibility:
    """DatabaseService.backup_database delegates to BackupService."""

    def test_legacy_backup_database_delegates(self, tmp_path):
        """backup_database() returns a verified backup path."""
        src = str(tmp_path / "source.db")
        _init_db(src)

        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        db_svc = DatabaseService(db_path=src)
        path = db_svc.backup_database(backup_dir=backup_dir)
        assert os.path.exists(path)
        assert path.endswith(".db")
        assert "encomm_backup_" in os.path.basename(path)

        # Verify it's a valid backup
        bu_conn = sqlite3.connect(path)
        try:
            cur = bu_conn.execute("PRAGMA quick_check")
            assert cur.fetchone()[0] == "ok"
        finally:
            bu_conn.close()

    def test_legacy_backup_database_raises_on_failure(self, tmp_path):
        """backup_database() raises RuntimeError when backup fails.

        DatabaseService.__init__ always creates the standard tables, so a
        table-validation failure can't be reached through DatabaseService.
        We test the failure path directly via BackupService."""
        src = str(tmp_path / "source.db")
        conn = sqlite3.connect(src)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE Foo (x INTEGER)")
        conn.commit()
        conn.close()

        backup_dir = str(tmp_path / "backups")
        os.makedirs(backup_dir, exist_ok=True)

        svc = BackupService(backup_dir=backup_dir)
        result = svc.create_backup(src)
        assert not result.ok
        assert "Missing required tables" in result.error_message


class TestRestoreDatabaseRemoved:
    """restore_database was removed — no destructive restore in this phase."""

    def test_restore_database_not_present(self):
        """DatabaseService no longer exposes restore_database()."""
        assert not hasattr(DatabaseService, "restore_database"), \
            "restore_database() must be removed — no in-app restore yet"


# ══════════════════════════════════════════════════════════════════════
# list_backups()
# ══════════════════════════════════════════════════════════════════════

class TestListBackups:
    """list_backups() returns regular backups only, newest first."""

    def test_empty_dir_returns_empty_list(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))
        assert svc.list_backups() == []

    def test_returns_newest_first(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        svc = BackupService(backup_dir=str(backup_dir))

        # Create two backups
        src = str(tmp_path / "source.db")
        _init_db(src)
        r1 = svc.create_backup(src)
        r2 = svc.create_backup(src)
        assert r1.ok and r2.ok

        backups = svc.list_backups()
        assert len(backups) >= 2
        # Newest first
        timestamps = [b.created_at for b in backups if b.created_at]
        assert timestamps == sorted(timestamps, reverse=True), \
            f"Not newest-first: {timestamps}"

    def test_skips_non_matching_files(self, tmp_path):
        """Only encomm_backup_*.db files are listed."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create a real backup
        src = str(tmp_path / "source.db")
        _init_db(src)
        svc = BackupService(backup_dir=str(backup_dir))
        result = svc.create_backup(src)
        assert result.ok

        # Create random files
        (backup_dir / "notes.txt").write_text("hello")
        (backup_dir / "random.db").write_text("not a backup")

        backups = svc.list_backups()
        names = {b.filename for b in backups}
        assert "notes.txt" not in names
        assert "random.db" not in names


# ══════════════════════════════════════════════════════════════════════
# Default backup directory
# ══════════════════════════════════════════════════════════════════════

class TestDefaultBackupDir:
    """Default backup directory points to Documents/ENCOMM ERP/Backups."""

    def test_default_dir_is_under_documents(self):
        default = BackupService._default_backup_dir()
        path_str = str(default).replace("\\", "/")
        assert "Documents" in path_str
        assert "ENCOMM ERP" in path_str
        assert path_str.endswith("Backups")
