"""ENCOMM ERP — PySide6 / Qt Application Entry Point.

This is the active Qt pilot entry point.  DatabaseService is initialized
from the configured path before the MainWindow is constructed so that
every page receives a live database service from startup.

Usage
-----
    python qt_main.py

The factory ``create_main_window()`` is importable by tests and by the
real entry point; it returns (app, window) without starting the event
loop so tests can safely navigate pages against a fresh temporary DB.
"""

from __future__ import annotations

import os
import sys
from typing import Tuple

from PySide6.QtWidgets import QApplication, QMainWindow

from infrastructure.database_service import DatabaseService
from qt_app.styles import DARK_PALETTE, GLOBAL_QSS
from qt_app.main_window import MainWindow


def create_main_window(
    db_path: str = "",
    app_name: str = "ENCOMM ERP",
) -> Tuple[QApplication, QMainWindow]:
    """Build the configured Qt application and main window.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.  Falls back to the ``DB_PATH``
        environment variable and finally to ``"encomm_erp.db"``.
    app_name : str
        QApplication name (default ``"ENCOMM ERP"``).

    Returns
    -------
    (app, window)
        The QApplication (already styled) and the MainWindow (not yet
        shown).  The caller is responsible for calling ``app.exec()``
        or cleaning up the window in tests.
    """
    resolved_path = db_path or os.getenv("DB_PATH", "encomm_erp.db")

    existing = QApplication.instance()
    app = existing or QApplication(sys.argv)
    if not existing:
        # Only set global style on a brand-new QApp instance
        app.setApplicationName(app_name)
        app.setOrganizationName("ENCOMM")
        app.setPalette(DARK_PALETTE)
        app.setStyleSheet(GLOBAL_QSS)

    db_service = DatabaseService(db_path=resolved_path)
    config: dict = {"db_path": resolved_path, "theme": "Dark"}
    window = MainWindow(db_service=db_service, config=config)

    return app, window


def main() -> None:
    """Real entry point — create the window and start the event loop."""
    app, window = create_main_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
