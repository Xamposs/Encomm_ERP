"""ENCOMM ERP — PySide6 / Qt Application Entry Point.

This is the future production entry point once the Qt migration is complete.
Currently a navigation-only shell with placeholder pages.

Usage
-----
    python qt_main.py

The existing CustomTkinter application (``python main.py``) remains the
default entry point until the Qt migration is complete and approved.
"""

import sys
import os

from PySide6.QtWidgets import QApplication

from qt_app.styles import DARK_PALETTE, GLOBAL_QSS
from qt_app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ENCOMM ERP")
    app.setOrganizationName("ENCOMM")
    app.setPalette(DARK_PALETTE)
    app.setStyleSheet(GLOBAL_QSS)

    config = {
        "db_path": os.getenv("DB_PATH", "encomm_erp.db"),
        "theme": "Dark",
    }
    window = MainWindow(config=config)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
