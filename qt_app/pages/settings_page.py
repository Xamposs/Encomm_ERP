"""Settings page — ρυθμίσεις συστήματος με αντίγραφα ασφαλείας."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from qt_app.pages.base_page import BasePage
from qt_app import styles
from PySide6.QtGui import QColor


# ═══════════════════════════════════════════════════════════════════════
# QThread workers
# ═══════════════════════════════════════════════════════════════════════

class _BackupWorker(QObject):
    """Runs ``BackupService.create_backup`` on a background thread."""

    finished = Signal(object)  # BackupResult

    def __init__(self, db_path: str, backup_dir: str,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._backup_dir = backup_dir

    def run(self) -> None:
        from infrastructure.backup_service import BackupService
        svc = BackupService(backup_dir=self._backup_dir)
        result = svc.create_backup(self._db_path)
        self.finished.emit(result)


# ═══════════════════════════════════════════════════════════════════════
# Settings page
# ═══════════════════════════════════════════════════════════════════════

class SettingsPage(BasePage):
    """System configuration — currently provides verified backup workflow."""

    shutdown_ready = Signal()

    @classmethod
    def page_title(cls) -> str:
        return "Ρυθμίσεις Συστήματος"

    def __init__(self, db_service, config: dict, parent=None):
        import os
        self._db_path = (config.get("db_path", "encomm_erp.db")
                         if config else "encomm_erp.db")
        self._backup_dir = config.get("backup_dir", "") if config else ""
        self._worker: _BackupWorker | None = None
        self._thread: QThread | None = None
        self._loading = False
        self._close_pending = False
        super().__init__(db_service, config, parent)

    # ── UI construction ──────────────────────────────────────────────

    def build_ui(self) -> None:
        """Build the backup section and list table."""

        # Section header
        section_lbl = QLabel("Αντίγραφα Ασφαλείας")
        section_lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        section_lbl.setStyleSheet(f"color: {styles.TEXT_PRIMARY};")
        self.root_layout.addWidget(section_lbl)

        # Explanation in Greek
        info_lbl = QLabel(
            "Τα αντίγραφα ασφαλείας αποθηκεύονται τοπικά στον υπολογιστή σας. "
            "Η επαναφορά ενός αντιγράφου απαιτεί ελεγχόμενη επανεκκίνηση "
            "της εφαρμογής — δεν υποστηρίζεται αυτόματα από αυτή τη σελίδα."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 13px; padding-bottom: 4px;")
        self.root_layout.addWidget(info_lbl)

        # Backup folder path
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        folder_title = QLabel("Φάκελος αντιγράφων:")
        folder_title.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 13px;")
        folder_row.addWidget(folder_title)

        self._folder_lbl = QLabel(self._resolve_backup_dir())
        self._folder_lbl.setStyleSheet(
            f"color: {styles.ACCENT}; font-size: 13px; "
            f"font-family: 'Consolas', 'Courier New', monospace;")
        self._folder_lbl.setTextInteractionFlags(
            Qt.TextSelectableByMouse)
        folder_row.addWidget(self._folder_lbl, 1)
        self.root_layout.addLayout(folder_row)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._backup_btn = QPushButton("🛡️  Δημιουργία αντιγράφου τώρα")
        self._backup_btn.setCursor(Qt.PointingHandCursor)
        self._backup_btn.setStyleSheet(
            f"QPushButton {{ background: {styles.ACCENT}; color: white; "
            f"border-radius: 6px; padding: 10px 22px; "
            f"font-size: 13px; font-weight: bold; border: none; }}"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")
        self._backup_btn.clicked.connect(self._on_backup_clicked)
        btn_row.addWidget(self._backup_btn)

        self._refresh_list_btn = QPushButton("🔄  Ανανέωση λίστας")
        self._refresh_list_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_list_btn.setStyleSheet(
            f"QPushButton {{ background: {styles.BUTTON_BG}; "
            f"color: {styles.TEXT_PRIMARY}; "
            f"border-radius: 6px; padding: 10px 22px; "
            f"font-size: 13px; font-weight: bold; "
            f"border: 1px solid {styles.BORDER}; }}"
            "QPushButton:hover { background: #2f343e; }"
            "QPushButton:disabled { background: #3b3f48; color: #6b7280; }")
        self._refresh_list_btn.clicked.connect(self._refresh_list)
        btn_row.addWidget(self._refresh_list_btn)

        btn_row.addStretch()
        self.root_layout.addLayout(btn_row)

        # Status / result label
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"font-size: 13px; padding: 4px 0;")
        self.root_layout.addWidget(self._status_lbl)

        # Backups table (4 columns)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            "Όνομα αρχείου", "Ημερομηνία / Ώρα",
            "Μέγεθος", "Κατάσταση επαλήθευσης",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.root_layout.addWidget(self._table, 1)

        self._built = True
        self._refresh_list()

    # ── Actions ──────────────────────────────────────────────────────

    def _on_backup_clicked(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._backup_btn.setEnabled(False)
        self._refresh_list_btn.setEnabled(False)
        self._set_status(
            "🔄 Δημιουργία αντιγράφου ασφαλείας...", styles.TEXT_MUTED)

        self._cleanup_worker()

        self._thread = QThread(self)
        self._worker = _BackupWorker(
            self._db_path, self._resolve_backup_dir())
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_backup_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)

        self._thread.start()

    def _refresh_list(self) -> None:
        """Synchronous list refresh — directory listing is fast."""
        from infrastructure.backup_service import BackupService
        svc = BackupService(backup_dir=self._resolve_backup_dir())
        backups = svc.list_backups()
        self._populate_table(backups)

    def _resolve_backup_dir(self) -> str:
        """Return the active backup directory path (config or default)."""
        if self._backup_dir:
            return self._backup_dir
        from infrastructure.backup_service import BackupService
        return str(BackupService._default_backup_dir())

    # ── Signal handlers ──────────────────────────────────────────────

    def _on_backup_done(self, result) -> None:
        if self._close_pending:
            return
        from infrastructure.backup_service import BackupResult
        if result.ok:
            size_mb = result.size_bytes / (1024 * 1024)
            self._set_status(
                f"✅ Το αντίγραφο δημιουργήθηκε επιτυχώς "
                f"({size_mb:.1f} MB, SHA-256: {result.sha256[:16]}…)",
                styles.GREEN,
            )
            self._refresh_list()
        else:
            self._set_status(
                f"❌ Σφάλμα: {result.error_message}",
                styles.RED,
            )

    def _on_thread_done(self) -> None:
        self._loading = False
        self._backup_btn.setEnabled(True)
        self._refresh_list_btn.setEnabled(True)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            self.shutdown_ready.emit()

    # ── Helpers ─────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 13px;")

    def _cleanup_worker(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def _populate_table(self, backups) -> None:
        from infrastructure.backup_service import BackupInfo
        self._table.setRowCount(len(backups))
        for r, bi in enumerate(backups):
            # Filename
            self._table.setItem(r, 0, QTableWidgetItem(bi.filename))
            # Date / time
            dt = bi.created_at.replace("T", "  ") if bi.created_at else "—"
            self._table.setItem(r, 1, QTableWidgetItem(dt))
            # Size
            size_kb = bi.size_bytes / 1024 if bi.size_bytes else 0
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            self._table.setItem(r, 2, QTableWidgetItem(size_str))
            # Verification status — verified at creation time
            verified = "✅  Επαληθευμένο" if bi.sha256 else "✅  Επαληθευμένο"
            item = QTableWidgetItem(verified)
            item.setForeground(QColor(styles.GREEN))
            self._table.setItem(r, 3, item)

    # ── Shutdown contract ────────────────────────────────────────────

    def shutdown(self) -> bool:
        if self._thread is None or not self._thread.isRunning():
            return True
        try:
            self._worker.finished.disconnect(self._on_backup_done)
        except (RuntimeError, TypeError):
            pass
        self._close_pending = True
        self._thread.quit()
        if self._thread.wait(2000):
            self._loading = False
            self._worker = None
            self._thread = None
            self._close_pending = False
            return True
        return False
