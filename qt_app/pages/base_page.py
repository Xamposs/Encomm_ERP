"""Base page class for all ENCOMM ERP Qt views.

Every page is a QWidget that receives:
    - db_service  — DatabaseService (Infrastructure layer)
    - config      — application-level configuration dict

Subclasses override ``build_ui()`` to create their widgets.  The
page-title label lives in the MainWindow header — pages should NOT
repeat it.

Every page has a solid DARK_BG background so that QStackedWidget
switching never leaks content from a previous page.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from qt_app import styles


class BasePage(QWidget):
    """Abstract base for all ERP Qt pages."""

    def __init__(self, db_service, config: dict, parent=None):
        super().__init__(parent)
        self.db_service = db_service
        self.config = config

        # Opaque surface — prevents QStackedWidget from showing
        # remnants of the previous page through a transparent background.
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(styles.DARK_BG))
        self.setPalette(pal)

        # Every page has a vertical root layout
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(30, 25, 30, 25)
        self.root_layout.setSpacing(16)

        self.build_ui()

    def build_ui(self) -> None:
        """Create all child widgets.  Called once from __init__.

        The default implementation places a placeholder label.  Subclasses
        override this to build their actual UI (tables, forms, buttons, etc.).
        """
        placeholder = QLabel(
            "Έτοιμο για μετάβαση από το CustomTkinter.\n"
            "Η σελίδα αυτή θα ενεργοποιηθεί κατά τη σταδιακή μετεγκατάσταση.")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(
            "color: #8a8f98; font-size: 15px; padding: 40px;")
        placeholder.setWordWrap(True)
        self.root_layout.addWidget(placeholder, 1)

    def refresh(self) -> None:
        """Refresh page data from the database.  Override in subclasses."""
        pass
