"""Restore preparation service — offline-only, never inside the running Qt process.

Prepares a restore request that a separate offline helper process executes
only after the ERP parent process has exited.  The running Qt application
never overwrites its own active database.

Usage::

    from infrastructure.restore_service import RestoreService, RestorePreparation

    svc = RestoreService()
    prep = svc.prepare_restore(
        selected_backup="C:\\backups\\encomm_backup_20260718_120000.db",
        active_db_path="C:\\ERP\\encomm_erp.db",
        backup_dir="C:\\backups",
        parent_pid=os.getpid(),
    )
    if prep.ok:
        # Write the request file; the helper processes it later
        import subprocess, sys
        QProcess.startDetached(sys.executable, [
            "-m", "infrastructure.restore_helper",
            "--request", prep.request_path,
        ])
        # Then close the app — the helper waits and restores offline.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

_REQUEST_SUFFIX = "_restore_request.json"
_STATUS_SUFFIX = "_restore_status.json"


@dataclass
class RestorePreparation:
    """Result of preparing a restore — consumed by the offline helper."""

    ok: bool
    request_path: str = ""
    request_id: str = ""
    selected_backup_path: str = ""
    active_db_path: str = ""
    pre_restore_backup_path: str = ""
    status_path: str = ""
    error_message: str = ""


@dataclass
class RestoreStatus:
    """Outcome of a restore helper run, read by the UI on next launch."""

    request_id: str
    success: bool
    timestamp: str
    message: str        # Greek-readable, deployment-safe
    status_path: str = ""


class RestoreService:
    """Prepare a controlled offline restore — never execute in-process."""

    def __init__(self) -> None:
        pass

    def prepare_restore(
        self,
        selected_backup: str | Path,
        active_db_path: str | Path,
        backup_dir: str | Path,
        parent_pid: int,
    ) -> RestorePreparation:
        """Prepare a restore request for the offline helper.

        Steps:
        1. Resolve all paths to absolute.
        2. Verify the selected backup (read-only).
        3. Create a verified pre-restore backup of the active database.
        4. Write a JSON restore request file for the helper.
        5. Return the preparation result.

        On any failure before step 4, returns ``ok=False`` and does
        NOT schedule anything.
        """
        selected = Path(selected_backup).resolve()
        active = Path(active_db_path).resolve()
        bk_dir = Path(backup_dir).resolve()

        # ── 1. Verify selected backup ──────────────────────────
        from infrastructure.backup_service import BackupService
        backup_svc = BackupService(backup_dir=str(bk_dir))
        verification = backup_svc.verify_backup(str(selected))
        if not verification.ok:
            return RestorePreparation(
                ok=False,
                selected_backup_path=str(selected),
                active_db_path=str(active),
                error_message=(
                    f"Το επιλεγμένο αντίγραφο δεν επαληθεύτηκε: "
                    f"{verification.error_message}"
                ),
            )

        # ── 2. Create pre-restore backup of active DB ───────────
        pre_restore_result = backup_svc.create_backup(str(active))
        if not pre_restore_result.ok:
            return RestorePreparation(
                ok=False,
                selected_backup_path=str(selected),
                active_db_path=str(active),
                error_message=(
                    "Αδυναμία δημιουργίας αντιγράφου ασφαλείας πριν "
                    f"την επαναφορά: {pre_restore_result.error_message}"
                ),
            )
        pre_restore_path = Path(pre_restore_result.backup_path)

        # ── 3. Verify pre-restore backup BEFORE writing request ──
        # This is the last safety net — it must be verified before we
        # schedule anything.
        pre_verify = backup_svc.verify_backup(str(pre_restore_path))
        if not pre_verify.ok:
            # Remove the pre-restore backup file — it's not usable.
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

        # ── 4. Write restore request ────────────────────────────
        request_id = f"restore_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        request_name = f"{request_id}{_REQUEST_SUFFIX}"
        request_path = bk_dir / request_name

        status_name = f"{request_id}{_STATUS_SUFFIX}"
        status_path = bk_dir / status_name

        request_data = {
            "request_id": request_id,
            "selected_backup_path": str(selected),
            "active_db_path": str(active),
            "pre_restore_backup_path": str(pre_restore_path),
            "parent_pid": parent_pid,
            "status_path": str(status_path),
        }

        # Atomic write via temp + rename
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=".tmp", prefix="restore_req_", dir=str(bk_dir)
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_path_str)
        try:
            tmp_path.write_text(
                json.dumps(request_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(request_path)
        except Exception:
            _remove_if_exists(tmp_path)
            raise

        logger.info(
            "Restore request written: %s (backup=%s, active=%s, pre_restore=%s)",
            request_path, selected, active, pre_restore_path,
        )

        return RestorePreparation(
            ok=True,
            request_path=str(request_path),
            request_id=request_id,
            selected_backup_path=str(selected),
            active_db_path=str(active),
            pre_restore_backup_path=str(pre_restore_path),
            status_path=str(status_path),
        )


def read_latest_status(db_path: str | Path, backup_dir: str | Path) -> RestoreStatus | None:
    """Find the latest restore status for *db_path* in *backup_dir*.

    Scans JSON status files, matches against the active database path,
    and returns the most recent matching ``RestoreStatus`` or ``None``.
    """
    target = Path(db_path).resolve()
    bk_dir = Path(backup_dir)

    matching: list[RestoreStatus] = []
    try:
        for entry in bk_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.endswith(_STATUS_SUFFIX):
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # Must have the right shape
            if not all(k in data for k in ("request_id", "success", "timestamp", "message")):
                continue
            # Only match if the request was for this database
            active_in_status = data.get("active_db_path", "")
            if active_in_status:
                try:
                    if Path(active_in_status).resolve() != target:
                        continue
                except (OSError, ValueError):
                    continue
            status = RestoreStatus(
                request_id=data["request_id"],
                success=data["success"],
                timestamp=data["timestamp"],
                message=data["message"],
                status_path=str(entry),
            )
            matching.append(status)
    except OSError:
        pass

    if not matching:
        return None
    # Return the most recent (lexicographic sort by request_id is fine
    # since it contains a timestamp)
    matching.sort(key=lambda s: s.request_id, reverse=True)
    return matching[0]


def _remove_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
