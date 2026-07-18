"""Offline database restore helper — runs as a separate process after ERP exits.

Invoked by the Qt Settings page via QProcess.startDetached()::

    python -m infrastructure.restore_helper --request <absolute-request-path>

The helper:
1. Reads and validates the request JSON defensively.
2. Waits for the parent ERP process to exit (bounded timeout).
3. Re-verifies the selected backup.
4. Restores via ``sqlite3.Connection.backup()`` into a temp file in the
   target database directory.
5. Verifies the temp restored DB.
6. Atomically replaces the active database (WAL/SHM companions handled).
7. Writes an atomic JSON status file.
8. Cleans up temp and consumed files.
9. Never uses ``shutil.copy2()`` to restore a database.
10. Never restarts the app.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

log_format = "%(asctime)s [restore-helper] %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stderr)
logger = logging.getLogger("restore_helper")

# ── Timeout for parent-process-exit wait ─────────────────────────────────
_DEFAULT_WAIT_TIMEOUT_S = 60


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline database restore helper — NEVER run inside the ERP process."
    )
    parser.add_argument(
        "--request", required=True,
        help="Absolute path to the restore request JSON file.",
    )
    parser.add_argument(
        "--wait-timeout", type=int, default=_DEFAULT_WAIT_TIMEOUT_S,
        help=f"Max seconds to wait for parent ERP exit (default: {_DEFAULT_WAIT_TIMEOUT_S}).",
    )
    args = parser.parse_args(argv)

    request_path = Path(args.request).resolve()

    # ── Read and validate the request ────────────────────────────
    try:
        request = _read_request(request_path)
    except Exception as exc:
        logger.error("Failed to read request: %s", exc)
        return 1

    # ── Execute the restore ──────────────────────────────────────
    try:
        status = execute_request(
            request,
            wait_timeout=int(args.wait_timeout),
        )
    except Exception as exc:
        logger.exception("Restore helper failed unexpectedly")
        status = {
            "request_id": request.get("request_id", "unknown"),
            "success": False,
            "timestamp": datetime.now().isoformat(),
            "message": f"Απρόσμενο σφάλμα βοηθού επαναφοράς: {exc}",
            "active_db_path": request.get("active_db_path", ""),
        }

    # ── Write status file ────────────────────────────────────────
    _write_status(request.get("status_path", ""), status)

    # ── Clean up consumed request file ───────────────────────────
    _remove_if_exists(request_path)

    return 0 if status["success"] else 1


# ═══════════════════════════════════════════════════════════════════
# Core logic (testable)
# ═══════════════════════════════════════════════════════════════════

def _read_request(path: Path) -> Dict[str, Any]:
    """Read and validate the restore request JSON defensively."""
    if not path.is_file():
        raise FileNotFoundError(f"Request file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    required = [
        "request_id", "selected_backup_path", "active_db_path",
        "pre_restore_backup_path", "parent_pid", "status_path",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    return data


def execute_request(request: Dict[str, Any], wait_timeout: int = _DEFAULT_WAIT_TIMEOUT_S) -> Dict[str, Any]:
    """Execute a validated restore request.

    Returns the status dict to be written to the status file.
    """
    request_id: str = request["request_id"]
    selected_backup: str = request["selected_backup_path"]
    active_db_path: str = request["active_db_path"]
    pre_restore_backup: str = request["pre_restore_backup_path"]
    parent_pid: int = request["parent_pid"]
    status_path: str = request["status_path"]

    selected = Path(selected_backup)
    active = Path(active_db_path)
    pre_restore = Path(pre_restore_backup)

    def _fail(message: str) -> Dict[str, Any]:
        logger.error("Restore failed: %s", message)
        return {
            "request_id": request_id,
            "success": False,
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "active_db_path": active_db_path,
        }

    # ── 1. Wait for parent ERP process to exit ───────────────────
    logger.info("Waiting for parent ERP (PID %d) to exit (timeout %ds)…",
                parent_pid, wait_timeout)
    try:
        if not _wait_for_parent_exit(parent_pid, wait_timeout):
            return _fail(
                f"Η εφαρμογή ERP δεν τερματίστηκε εντός {wait_timeout} "
                "δευτερολέπτων. Η επαναφορά ακυρώθηκε."
            )
    except Exception as exc:
        return _fail(f"Σφάλμα αναμονής τερματισμού ERP: {exc}")
    logger.info("Parent ERP exited.")

    # ── 2. Re-verify selected backup ─────────────────────────────
    logger.info("Re-verifying selected backup: %s", selected)
    from infrastructure.backup_service import BackupService
    backup_svc = BackupService(backup_dir=str(active.parent))
    verification = backup_svc.verify_backup(str(selected))
    if not verification.ok:
        return _fail(
            f"Το επιλεγμένο αντίγραφο απέτυχε στην επανεπαλήθευση: "
            f"{verification.error_message}"
        )
    logger.info("Backup verified — SHA-256: %s", verification.sha256[:16])

    # ── 3. Restore via sqlite3 backup() into temp file ───────────
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".db", prefix="restored_", dir=str(active.parent)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    logger.info("Restoring into temp: %s", tmp_path)

    try:
        _sqlite_restore_into(selected, tmp_path)
    except Exception as exc:
        _remove_if_exists(tmp_path)
        return _fail(f"Η αντιγραφή της βάσης δεδομένων απέτυχε: {exc}")

    # ── 4. Verify restored temp DB ───────────────────────────────
    temp_verif = backup_svc.verify_backup(str(tmp_path))
    if not temp_verif.ok:
        _remove_if_exists(tmp_path)
        return _fail(
            f"Η επαληθευμένη βάση δεδομένων απέτυχε στον έλεγχο: "
            f"{temp_verif.error_message}"
        )
    logger.info("Temp restored DB verified ok.")

    # ── 5. Replace active database ───────────────────────────────
    try:
        _replace_active_database(active, tmp_path)
    except Exception as exc:
        logger.exception("Replacement failed — attempting rollback")
        _remove_if_exists(tmp_path)
        return _fail(
            f"Η αντικατάσταση της βάσης δεδομένων απέτυχε: {exc}"
        )

    # ── 6. Success ───────────────────────────────────────────────
    logger.info("Restore complete. Active DB now matches backup.")
    return {
        "request_id": request_id,
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "message": "Η επαναφορά ολοκληρώθηκε με επιτυχία. "
                   "Μπορείτε να ανοίξετε ξανά την εφαρμογή.",
        "active_db_path": active_db_path,
    }


# ═══════════════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════════════

def _wait_for_parent_exit(pid: int, timeout_s: int) -> bool:
    """Return True if the process identified by *pid* exits within *timeout_s* seconds.

    Uses polling (sleeps 500 ms between checks).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.5)
    return not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    """Return True if a process with *pid* is running on this OS.

    Platform-safe: uses ``os.kill(pid, 0)`` on POSIX, ctypes on Windows.
    """
    if os.name == "nt":
        return _pid_exists_windows(pid)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _pid_exists_windows(pid: int) -> bool:
    """Check process existence on Windows via WaitForSingleObject."""
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle == 0:
        # Access denied or invalid PID — assume not running
        return False

    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        # If the process is still running, WaitForSingleObject
        # returns WAIT_TIMEOUT (0x00000102).  If it has exited,
        # it returns WAIT_OBJECT_0.
        return result != WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _sqlite_restore_into(source: Path, dest: Path) -> None:
    """Copy *source* into *dest* via ``Connection.backup()``.

    Opens source in read-only mode.
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


def _replace_active_database(active: Path, temp_restored: Path) -> None:
    """Atomically replace *active* with *temp_restored*.

    - Moves the current active DB (and WAL/SHM companions) aside.
    - Atomically moves *temp_restored* to the *active* path.
    - Removes stale WAL/SHM from the replaced DB.
    - If replacement fails at any step, restores the original DB.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    aside_path = active.with_name(f"{active.stem}_aside_{ts}{active.suffix}")

    # ── WAL / SHM companions ─────────────────────────────────────
    wal_path = Path(str(active) + "-wal")
    shm_path = Path(str(active) + "-shm")
    aside_wal = Path(str(aside_path) + "-wal")
    aside_shm = Path(str(aside_path) + "-shm")

    companions_moved: list[tuple[Path, Path]] = []

    try:
        # Move current active DB aside
        active.rename(aside_path)

        # Move WAL / SHM aside (if they exist)
        for companion, aside_companion in [
            (wal_path, aside_wal),
            (shm_path, aside_shm),
        ]:
            if companion.is_file():
                companion.rename(aside_companion)
                companions_moved.append((companion, aside_companion))

        # Atomically place the restored temp at the active path
        temp_restored.replace(active)

        # Remove WAL/SHM at the original companion paths (they belong
        # to the aside DB now — fresh restored DB starts without them).
        # Also remove any fresh WAL/SHM created by the restore itself
        # (the restored temp inherits WAL mode from the source backup).
        for orig_companion in [wal_path, shm_path]:
            _remove_if_exists(orig_companion)
        # The temp_restored replacement may create new WAL/SHM at the
        # active path — clean those too.
        _remove_if_exists(Path(str(active) + "-wal"))
        _remove_if_exists(Path(str(active) + "-shm"))

        # Clean up the aside DB (we have the pre-restore backup for safety)
        _remove_if_exists(aside_path)
        for _, aside_c in companions_moved:
            _remove_if_exists(aside_c)

    except Exception:
        logger.exception("Replacement failed — restoring original DB")

        # Try to put everything back
        if aside_path.is_file():
            try:
                aside_path.rename(active)
            except OSError:
                pass

        for orig_companion, aside_c in companions_moved:
            if aside_c.is_file() and not orig_companion.is_file():
                try:
                    aside_c.rename(orig_companion)
                except OSError:
                    pass

        raise


def _write_status(status_path_str: str, status: Dict[str, Any]) -> None:
    """Write the status JSON atomically."""
    if not status_path_str:
        logger.error("No status_path in request — cannot write status")
        return

    status_path = Path(status_path_str)
    status_dir = status_path.parent
    os.makedirs(str(status_dir), exist_ok=True)

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".tmp", prefix="restore_status_", dir=str(status_dir)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        tmp_path.write_text(
            json.dumps(status, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(status_path)
        logger.info("Status written to: %s (success=%s)", status_path, status["success"])
    except Exception:
        _remove_if_exists(tmp_path)
        raise


def _remove_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
