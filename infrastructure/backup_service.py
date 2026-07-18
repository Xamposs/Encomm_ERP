"""Verified SQLite backup service — pure stdlib, Connection.backup() based.

Creates self-contained database backups from a live WAL-mode SQLite source,
validates them, and atomically publishes them to a backup directory.

Usage::

    from infrastructure.backup_service import BackupService, BackupResult

    svc = BackupService()
    result = svc.create_backup("/path/to/source.db")
    if result.ok:
        print(f"Backed up to {result.backup_path}")
    backups = svc.list_backups()
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ── Timestamped filename pattern ──────────────────────────────────────────
# Used for backup files.  Partial (temp) files use a .tmp suffix that is
# never matched by list_backups().
_DIR_NAME = "ENCOMM ERP"
_SUB_NAME = "Backups"
_FILENAME_FMT = "encomm_backup_{timestamp}.db"

_REQUIRED_TABLES = frozenset({"ProductMaster", "SystemConfig"})


@dataclass
class BackupResult:
    """Typed result from a backup operation."""

    ok: bool
    backup_path: str = ""
    created_at: str = ""
    size_bytes: int = 0
    sha256: str = ""
    error_message: str = ""


@dataclass
class VerifyBackupResult:
    """Typed result from ``verify_backup()``."""

    ok: bool
    path: str = ""
    size_bytes: int = 0
    sha256: str = ""
    error_message: str = ""


@dataclass
class BackupInfo:
    """Lightweight entry for list_backups()."""

    filename: str
    path: str
    created_at: str   # ISO timestamp from filename
    size_bytes: int
    sha256: str = ""  # populated lazily if requested


class BackupService:
    """Verified local SQLite backup operations.

    - Uses ``sqlite3.Connection.backup()`` (never raw file copy).
    - Validates via ``PRAGMA quick_check`` + table-existence + SHA-256.
    - Publishes only after validation via atomic rename.
    - Default backup location: ``Documents\\ENCOMM ERP\\Backups\\``.
    """

    def __init__(self, backup_dir: str | Path | None = None):
        if backup_dir is None:
            backup_dir = self._default_backup_dir()
        self._backup_dir = Path(backup_dir)
        os.makedirs(str(self._backup_dir), exist_ok=True)

    # ── Public API ───────────────────────────────────────────────────────

    def verify_backup(self, backup_path: str | Path) -> VerifyBackupResult:
        """Verify a backup file read-only — never modifies the file.

        Checks:
        - path exists and is a regular file
        - SQLite opens read-only
        - PRAGMA quick_check returns ok
        - ProductMaster and SystemConfig exist
        - SHA-256 calculated

        Returns a ``VerifyBackupResult`` — check ``.ok``.
        """
        path = Path(backup_path)

        # ── File-level checks ─────────────────────────────────
        if not path.exists():
            return VerifyBackupResult(
                ok=False,
                path=str(path),
                error_message=f"Το αρχείο δεν υπάρχει: {path}",
            )
        if not path.is_file():
            return VerifyBackupResult(
                ok=False,
                path=str(path),
                error_message=f"Η διαδρομή δεν είναι κανονικό αρχείο: {path}",
            )

        sha256: str = ""
        try:
            # ── SQLite integrity ───────────────────────────────
            uri = f"file:{path.as_posix()}?mode=ro"
            try:
                conn = sqlite3.connect(uri, uri=True)
            except sqlite3.Error as e:
                return VerifyBackupResult(
                    ok=False,
                    path=str(path),
                    error_message=f"Αδυναμία ανοίγματος SQLite (mode=ro): {e}",
                )

            try:
                cur = conn.execute("PRAGMA quick_check")
                row = cur.fetchone()
                if row is None or row[0] != "ok":
                    detail = row[0] if row else "no result"
                    return VerifyBackupResult(
                        ok=False,
                        path=str(path),
                        error_message=f"PRAGMA quick_check απέτυχε: {detail}",
                    )

                # Required tables
                tables = {
                    r[0] for r in
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                missing = _REQUIRED_TABLES - tables
                if missing:
                    return VerifyBackupResult(
                        ok=False,
                        path=str(path),
                        error_message=(
                            "Λείπουν απαιτούμενοι πίνακες: "
                            f"{', '.join(sorted(missing))}"
                        ),
                    )
            finally:
                conn.close()

            # ── SHA-256 ────────────────────────────────────────
            sha256 = self._sha256_file(path)
            size = path.stat().st_size

            return VerifyBackupResult(
                ok=True,
                path=str(path),
                size_bytes=size,
                sha256=sha256,
            )

        except Exception as exc:
            return VerifyBackupResult(
                ok=False,
                path=str(path),
                error_message=str(exc),
            )

    def create_backup(
        self,
        source_db_path: str | Path,
    ) -> BackupResult:
        """Create a verified, timestamped backup of *source_db_path*.

        Returns a ``BackupResult`` — check ``.ok``.
        """
        source = Path(source_db_path)

        # ── Validate source BEFORE creating any temp files ────────────
        if not source.exists():
            return BackupResult(
                ok=False,
                error_message=f"Source database does not exist: {source}",
            )
        if not source.is_file():
            return BackupResult(
                ok=False,
                error_message=f"Source path is not a regular file: {source}",
            )

        created_at = datetime.now()
        ts = created_at.strftime("%Y%m%d_%H%M%S_%f")  # µs avoids same-second collision
        final_name = _FILENAME_FMT.format(timestamp=ts)
        final_path = self._backup_dir / final_name

        # Write to a temp sibling so the atomic rename stays on the same
        # filesystem (os.replace is atomic within the same volume).
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=".tmp",
            prefix="encomm_backup_",
            dir=str(self._backup_dir),
        )
        os.close(tmp_fd)  # we just need the path, not the fd
        tmp_path = Path(tmp_path_str)

        try:
            # 1. Backup via sqlite3 API
            self._sqlite_backup(source, tmp_path)

            # 2. Validate the temporary backup
            sha = self._validate_backup(tmp_path)

            # 3. Publish (atomic rename)
            tmp_path.replace(final_path)

            size = final_path.stat().st_size
            logger.info(
                "Backup verified and published: %s (%d bytes, sha256=%s)",
                final_path, size, sha,
            )
            return BackupResult(
                ok=True,
                backup_path=str(final_path),
                created_at=created_at.isoformat(timespec="seconds"),
                size_bytes=size,
                sha256=sha,
            )
        except Exception as exc:
            # Clean up partial temp file; never touch existing backups.
            self._remove_if_exists(tmp_path)
            msg = str(exc)
            logger.error("Backup failed: %s", msg)
            return BackupResult(
                ok=False,
                created_at=created_at.isoformat(timespec="seconds"),
                error_message=msg,
            )

    def list_backups(self) -> List[BackupInfo]:
        """Return regular backups (newest first), skipping partial files."""
        results: list[BackupInfo] = []
        if not self._backup_dir.is_dir():
            return results

        for entry in sorted(
            self._backup_dir.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if not entry.is_file():
                continue
            name = entry.name
            # Only include *.db files (never .tmp)
            if not name.endswith(".db"):
                continue
            if name.endswith(".tmp"):
                continue

            # Strict timestamp match — skip malformed names even if they
            # look similar.
            ts = self._parse_timestamp(name)
            if ts is None:
                continue

            results.append(BackupInfo(
                filename=name,
                path=str(entry),
                created_at=ts,
                size_bytes=entry.stat().st_size,
            ))
        return results

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _default_backup_dir() -> Path:
        """Return ``Documents/ENCOMM ERP/Backups`` on Windows."""
        docs = Path(os.path.expanduser("~")) / "Documents"
        return docs / _DIR_NAME / _SUB_NAME

    @staticmethod
    def _sqlite_backup(source: Path, dest: Path) -> None:
        """Copy *source* into *dest* via ``Connection.backup()``.

        Opens *source* in read-only mode so SQLite never creates a
        database file at the source path.
        """
        uri = f"file:{source.as_posix()}?mode=ro"
        src_conn = sqlite3.connect(uri, uri=True)
        try:
            dst_conn = sqlite3.connect(str(dest))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

    def _validate_backup(self, backup_file: Path) -> str:
        """Run integrity checks on *backup_file*.  Returns the SHA-256 hex.

        Raises ``RuntimeError`` on any validation failure so the caller
        can discard the temp file.
        """
        # Open read-only to avoid any accidental writes
        uri = f"file:{backup_file.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            # -- PRAGMA quick_check --
            cur = conn.execute("PRAGMA quick_check")
            row = cur.fetchone()
            if row is None or row[0] != "ok":
                detail = row[0] if row else "no result"
                raise RuntimeError(f"PRAGMA quick_check failed: {detail}")

            # -- Required tables --
            tables = {
                r[0] for r in
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = _REQUIRED_TABLES - tables
            if missing:
                raise RuntimeError(
                    f"Missing required tables: {', '.join(sorted(missing))}"
                )
        finally:
            conn.close()

        # -- SHA-256 (outside the connection so the file is closed) --
        return self._sha256_file(backup_file)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _remove_if_exists(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _parse_timestamp(filename: str) -> str | None:
        """Extract ISO timestamp from ``encomm_backup_YYYYMMDD_HHMMSS_ffffff.db``."""
        prefix = "encomm_backup_"
        suffix = ".db"
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            return None
        raw = filename[len(prefix):-len(suffix)]  # e.g. "20260718_143002_123456"
        if len(raw) < 15:
            return None
        try:
            # Parse with microseconds
            dt = datetime.strptime(raw, "%Y%m%d_%H%M%S_%f")
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return None
