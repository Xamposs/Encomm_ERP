"""Main window shell — sidebar + stacked pages + navigation.

The MainWindow owns:
- A fixed-width sidebar (220 px) with 9 Greek navigation buttons.
- A QStackedWidget that holds all 9 pages, one of which is visible.
- A status bar.

Navigation is via sidebar button clicks.  The active button is highlighted.
Pages are created lazily on first access and cached.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame,
    QStackedWidget, QStatusBar, QSizePolicy,
)

from qt_app import styles
from qt_app.pages import PAGE_CLASSES


# ── Sidebar nav item definitions ────────────────────────────────────────
NAV_ITEMS = [
    ("dashboard",        "📊  Αρχική"),
    ("inventory",        "📦  Αποθήκη"),
    ("suppliers",        "🏭  Προμηθευτές"),
    ("pos",              "🧾  Ταμείο / Πωλήσεις"),
    ("customers",        "👥  Πελάτες"),
    ("invoice_history",  "🔎  Ιστορικό"),
    ("stock_movements",  "📋  Κινήσεις"),
    ("settings",         "⚙️  Ρυθμίσεις"),
    ("ai_assistant",     "🤖  AI Βοηθός"),
]

PAGE_TITLES = {
    "dashboard":        "Επισκόπηση Συστήματος",
    "inventory":        "Διαχείριση Αποθήκης",
    "suppliers":        "Μητρώο Προμηθευτών",
    "pos":              "Ταμείο / Πωλήσεις (POS)",
    "customers":        "Μητρώο Πελατών",
    "invoice_history":  "Ιστορικό Παραστατικών",
    "stock_movements":  "Κινήσεις Αποθέματος",
    "settings":         "Ρυθμίσεις Συστήματος",
    "ai_assistant":     "AI Βοηθός",
}


class MainWindow(QMainWindow):
    """ENCOMM ERP Qt application shell."""

    def __init__(self, db_service=None, config: dict | None = None):
        super().__init__()

        self.db_service = db_service
        self.config = config or {}

        self.setWindowTitle("ENCOMM ERP 🧪")
        self.resize(1150, 730)
        self.setMinimumSize(1050, 650)

        # ── Central widget / root layout ──
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(
            f"QFrame {{ background: {styles.DARK_SURFACE}; }}")
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(16, 30, 16, 20)
        side_lay.setSpacing(6)

        # Brand
        brand = QLabel("ENCOMM ERP 🧪")
        brand.setFont(QFont("Segoe UI", 18, QFont.Bold))
        brand.setStyleSheet("color: #d0d4dc; padding-bottom: 12px;")
        side_lay.addWidget(brand)

        # Nav buttons
        self._nav_btns: dict[str, QPushButton] = {}
        self._current_page: str | None = None

        for key, label in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._nav_style(active=False))
            btn.clicked.connect(lambda checked, k=key: self.navigate_to(k))
            side_lay.addWidget(btn)
            self._nav_btns[key] = btn

        side_lay.addStretch()

        # Version
        ver = QLabel("v1.0.0 | ENCOMM Qt")
        ver.setStyleSheet(f"color: {styles.TEXT_DIM}; font-size: 10px;")
        side_lay.addWidget(ver)

        root.addWidget(sidebar)

        # ── Content area (stacked pages) ─────────────────────────────
        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        self._page_indices: dict[str, int] = {}

        for idx, (key, _label) in enumerate(NAV_ITEMS):
            # Placeholder — real page created lazily in _ensure_page()
            placeholder = QWidget()
            self._stack.addWidget(placeholder)
            self._page_indices[key] = idx

        root.addWidget(self._stack, 1)

        # ── Status bar ───────────────────────────────────────────────
        self._status_lbl = QLabel("Έτοιμο")
        self._status_lbl.setStyleSheet(
            f"color: {styles.GREEN}; padding: 2px 8px;")
        sb = QStatusBar()
        sb.addPermanentWidget(self._status_lbl)
        self.setStatusBar(sb)

        # ── Start on dashboard ───────────────────────────────────────
        self.navigate_to("dashboard")

    # ── Navigation ────────────────────────────────────────────────────
    def navigate_to(self, key: str) -> None:
        """Switch to the named page, creating it lazily if needed."""
        if self._current_page == key:
            return
        self._current_page = key

        # Highlight the active sidebar button
        for k, btn in self._nav_btns.items():
            btn.setStyleSheet(self._nav_style(active=(k == key)))

        # Ensure page is built
        page = self._ensure_page(key)
        idx = self._page_indices[key]
        self._stack.setCurrentIndex(idx)

        # Update status bar with page title
        self._status_lbl.setText(
            f"{PAGE_TITLES.get(key, key)}  —  Έτοιμο")

    def _ensure_page(self, key: str) -> QWidget:
        """Return the page widget, creating it on first access."""
        if key in self._pages:
            return self._pages[key]

        cls = PAGE_CLASSES.get(key)
        if cls is None:
            page = QWidget()
        else:
            page = cls(self.db_service, self.config)

        # Replace the placeholder in the stack
        idx = self._page_indices[key]
        old = self._stack.widget(idx)
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(idx, page)

        self._pages[key] = page
        return page

    # ── Sidebar button style ──────────────────────────────────────────
    @staticmethod
    def _nav_style(active: bool) -> str:
        if active:
            return (
                "QPushButton {"
                "  background: #252b36; color: #3B82F6; "
                "  border-radius: 8px; padding: 10px 14px; "
                "  text-align: left; font-weight: bold; "
                "  font-size: 13px; border: none; }"
            )
        return (
            "QPushButton {"
            "  background: transparent; color: #8a8f98; "
            "  border-radius: 8px; padding: 10px 14px; "
            "  text-align: left; font-size: 13px; border: none; }"
            "QPushButton:hover { background: #22252C; color: #b0b8c4; }"
        )
