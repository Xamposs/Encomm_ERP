"""Base page class for all ENCOMM ERP Qt views.

Every page is a QWidget that receives:
    - db_service  — DatabaseService (Infrastructure layer)
    - config      — application-level configuration dict

Subclasses override ``build_ui()`` to create their widgets and must
provide a ``page_title()`` classmethod returning the Greek display name.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QFont


class BasePage(QWidget):
    """Abstract base for all ERP Qt pages."""

    def __init__(self, db_service, config: dict, parent=None):
        super().__init__(parent)
        self.db_service = db_service
        self.config = config
        self._built = False

        # Every page has a vertical root layout
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(30, 25, 30, 25)
        self.root_layout.setSpacing(16)

        # Title bar
        self.title_label = QLabel(self.page_title())
        self.title_label.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self.title_label.setStyleSheet("color: #e0e4ec;")
        self.root_layout.addWidget(self.title_label)

        # Defer widget creation to first show — see build_ui()
        self.build_ui()

    @classmethod
    def page_title(cls) -> str:
        """Greek display name for this page (shown in the header)."""
        raise NotImplementedError("Subclass must implement page_title()")

    def build_ui(self) -> None:
        """Create all child widgets.  Called once from __init__.

        The default implementation places a placeholder label.  Subclasses
        override this to build their actual UI (tables, forms, buttons, etc.).
        """
        placeholder = QLabel(
            f"📋 {self.page_title()}\n\n"
            "Έτοιμο για μετάβαση από το CustomTkinter.\n"
            "Η σελίδα αυτή θα ενεργοποιηθεί κατά τη σταδιακή μετεγκατάσταση.")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(
            "color: #8a8f98; font-size: 15px; padding: 40px;")
        placeholder.setWordWrap(True)
        self.root_layout.addWidget(placeholder, 1)
        self._built = True

    def refresh(self) -> None:
        """Refresh page data from the database.  Override in subclasses."""
        pass
