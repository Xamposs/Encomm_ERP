"""Real-subprocess integration tests for the offline restore helper.

These tests launch ``python -m infrastructure.restore_helper --request <path>``
as a genuine child process.  No mocking of ``main()``, ``subprocess.run``,
``execute_request``, ``RestoreService.prepare_restore``, PID detection,
or SQLite backup/swap logic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# ── Project root (for subprocess cwd) ────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Test-only helpers ────────────────────────────────────────────────────

def _make_db(path: Path, /, products: list[tuple[str, str, int]] | None = None) -> None:
    """Create a minimal WAL-mode SQLite database with required tables."""
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
    if products:
        for barcode, name, stock in products:
            conn.execute(
                "INSERT OR REPLACE INTO ProductMaster "
                "(Barcode, Name, Stock, ExpiryDate, Price) "
                "VALUES (?, ?, ?, '2099-12-31', 5.0)",
                (barcode, name, stock),
            )
    conn.commit()
    conn.close()


def _read_stock(db_path: Path, barcode: str = "5200000000017") -> int | None:
    """Read the Stock value for a product from *db_path*."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode = ?",
            (barcode,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _read_name(db_path: Path, barcode: str = "5200000000017") -> str | None:
    """Read the Name value for a product from *db_path*."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT Name FROM ProductMaster WHERE Barcode = ?",
            (barcode,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _quick_check(db_path: Path) -> str:
    """Return the PRAGMA quick_check result for *db_path*."""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_helper(request_path: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run the real restore helper as a subprocess."""
    return subprocess.run(
        [
            sys.executable,
            "-m", "infrastructure.restore_helper",
            "--request", request_path,
        ],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        timeout=timeout,
    )


def _finished_child_pid() -> int:
    """Return the PID of a short-lived child process that has already exited.

    Spawns ``python -c \"pass\"``, waits for it to finish, and returns its
    PID.  The helper's parent-exit guard sees this as a dead process and
    proceeds immediately — no magic PID, no race.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = proc.pid
    proc.wait(timeout=10)
    return pid


# ══════════════════════════════════════════════════════════════════════════
# Success path
# ══════════════════════════════════════════════════════════════════════════

def test_helper_subprocess_success(tmp_path):
    """The real helper subprocess restores the active DB from a valid
    backup and reports success."""
    # ── 1. Create three databases ─────────────────────────────────
    active = tmp_path / "active.db"
    _make_db(active, products=[("5200000000017", "Ενεργό Προϊόν", 200)])

    selected = tmp_path / "selected_backup.db"
    _make_db(selected, products=[("5200000000017", "Αποκατεστημένο Προϊόν", 10)])

    pre_restore = tmp_path / "pre_restore_backup.db"
    _make_db(pre_restore, products=[("5200000000017", "Pre-Restore Copy", 200)])

    # ── 2. Write the restore request ──────────────────────────────
    request_path = tmp_path / "e2e_success_restore_request.json"
    status_path = tmp_path / "e2e_success_restore_status.json"

    # Hash pre-restore for later comparison
    pre_hash_before = _hash_file(pre_restore)

    request_data = {
        "request_id": "e2e_success",
        "selected_backup_path": str(selected.resolve()),
        "active_db_path": str(active.resolve()),
        "pre_restore_backup_path": str(pre_restore.resolve()),
        "parent_pid": _finished_child_pid(),
        "status_path": str(status_path.resolve()),
    }
    request_path.write_text(
        json.dumps(request_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── 3. Run the real helper ────────────────────────────────────
    proc = _run_helper(str(request_path))

    # ── 4. Assert success ─────────────────────────────────────────
    assert proc.returncode == 0, (
        f"Helper exited {proc.returncode}\n"
        f"STDERR: {proc.stderr.decode(errors='replace')[-2000:]}"
    )

    # Request file should be consumed
    assert not request_path.exists(), (
        "Helper must consume the request file after completion"
    )

    # Status file must exist and report success
    assert status_path.is_file(), "Status file must exist"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["success"] is True, f"Status reports failure: {status.get('message')}"
    assert status["request_id"] == "e2e_success"

    # Active DB now contains the selected backup's data
    assert _read_name(active) == "Αποκατεστημένο Προϊόν"
    assert _read_stock(active) == 10

    # Active DB passes quick_check
    assert _quick_check(active) == "ok"

    # Pre-restore backup unchanged
    assert pre_restore.is_file(), "Pre-restore backup must be retained"
    assert _hash_file(pre_restore) == pre_hash_before, (
        "Pre-restore backup must be unchanged"
    )

    # No temporary restore artifacts in tmp_path
    all_files = {p.name for p in tmp_path.iterdir()}
    temp_artifacts = [
        n for n in all_files
        if n.startswith("restored_") or n.startswith("restore_req_")
        or n.startswith("restore_status_")
    ]
    assert not temp_artifacts, (
        f"Temporary restore artifacts should be cleaned: {temp_artifacts}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Failure path — corrupt pre-restore backup
# ══════════════════════════════════════════════════════════════════════════

def test_helper_subprocess_fails_on_corrupt_pre_restore(tmp_path):
    """When the pre-restore backup is corrupt, the helper subprocess
    reports failure and leaves the active DB unchanged."""
    # ── 1. Create databases ───────────────────────────────────────
    active = tmp_path / "active.db"
    _make_db(active, products=[("5200000000017", "Αρχικό Δεδομένο", 77)])

    selected = tmp_path / "selected_backup.db"
    _make_db(selected, products=[("5200000000017", "Backup Data", 5)])

    pre_restore = tmp_path / "pre_restore_backup.db"
    _make_db(pre_restore, products=[("5200000000017", "Pre-Restore", 77)])

    # Hash + copy active DB for byte-for-byte comparison
    active_hash_before = _hash_file(active)
    active_data_before = active.read_bytes()

    # ── 2. Write the request ──────────────────────────────────────
    request_path = tmp_path / "e2e_fail_restore_request.json"
    status_path = tmp_path / "e2e_fail_restore_status.json"

    request_data = {
        "request_id": "e2e_fail",
        "selected_backup_path": str(selected.resolve()),
        "active_db_path": str(active.resolve()),
        "pre_restore_backup_path": str(pre_restore.resolve()),
        "parent_pid": _finished_child_pid(),
        "status_path": str(status_path.resolve()),
    }
    request_path.write_text(
        json.dumps(request_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── 3. Corrupt the pre-restore backup AFTER request creation ──
    pre_restore.write_bytes(pre_restore.read_bytes()[:16])

    # ── 4. Run the real helper ────────────────────────────────────
    proc = _run_helper(str(request_path))

    # ── 5. Assert failure ─────────────────────────────────────────
    assert proc.returncode != 0, (
        "Helper must exit non-zero when pre-restore backup is corrupt\n"
        f"STDERR: {proc.stderr.decode(errors='replace')[-2000:]}"
    )

    # Request file should still be consumed (helper always cleans up)
    assert not request_path.exists(), "Helper must consume the request file"

    # Status file must exist and report failure
    assert status_path.is_file(), "Status file must exist"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["success"] is False, "Status must report failure"
    assert status["request_id"] == "e2e_fail"

    # Active DB unchanged (byte-for-byte)
    assert active.read_bytes() == active_data_before, (
        "Active DB must be byte-for-byte unchanged"
    )
    assert _hash_file(active) == active_hash_before

    # Active DB still has original data — no backup data was applied
    assert _read_name(active) == "Αρχικό Δεδομένο"
    assert _read_stock(active) == 77
