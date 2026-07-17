"""Main window shell — sidebar + stacked pages + navigation.

The MainWindow owns:
- A fixed-width sidebar (220 px) with 10 Greek navigation buttons.
- A QStackedWidget that holds all 10 pages, one of which is visible.
- A status bar.

Navigation is via sidebar button clicks.  The active button is highlighted.
Pages are created lazily on first access and cached.
"""

from PySide6.QtCore import Qt, QTimer, QDateTime, QLocale
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QButtonGroup,
    QStackedWidget, QStatusBar, QSizePolicy,
)

from qt_app import styles
from qt_app.pages import PAGE_CLASSES


# ── Sidebar nav item definitions ────────────────────────────────────────
NAV_ITEMS = [
    ("dashboard",        "📊  Αρχική"),
    ("inventory",        "📦  Αποθήκη"),
    ("suppliers",        "🏭  Προμηθευτές"),
    ("goods_receipts",   "🚚  Παραλαβές"),
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
    "goods_receipts":   "Παραλαβές Προμηθευτών",
    "pos":              "Ταμείο / Πωλήσεις (POS)",
    "customers":        "Μητρώο Πελατών",
    "invoice_history":  "Ιστορικό Παραστατικών",
    "stock_movements":  "Κινήσεις Αποθέματος",
    "settings":         "Ρυθμίσεις Συστήματος",
    "ai_assistant":     "AI Βοηθός",
}

# ── Sidebar button QSS (active state via :checked pseudo-class) ───────
NAV_QSS = (
    "QPushButton {"
    "  background: transparent; color: #8a8f98; "
    "  border-radius: 8px; padding: 10px 14px; "
    "  text-align: left; font-size: 13px; border: none; }"
    "QPushButton:hover { background: #22252C; color: #b0b8c4; }"
    "QPushButton:checked {"
    "  background: #252b36; color: #3B82F6; "
    "  font-weight: bold; }"
)


class MainWindow(QMainWindow):
    """ENCOMM ERP Qt application shell."""

    def __init__(self, db_service=None, config: dict | None = None):
        super().__init__()

        self.db_service = db_service
        self.config = config or {}
        self._current_page: str | None = None

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

        # Nav buttons (exclusive QButtonGroup)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_keys: list[str] = []

        for idx, (key, label) in enumerate(NAV_ITEMS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(NAV_QSS)
            self._nav_group.addButton(btn, idx)
            side_lay.addWidget(btn)
            self._nav_keys.append(key)

        self._nav_group.idClicked.connect(self._on_nav_clicked)

        side_lay.addStretch()

        # Version
        ver = QLabel("v1.0.0 | ENCOMM Qt")
        ver.setStyleSheet(f"color: {styles.TEXT_DIM}; font-size: 10px;")
        side_lay.addWidget(ver)

        root.addWidget(sidebar)

        # ── Content area (header + stacked pages) ──────────────────────
        content_wrapper = QWidget()
        content_wrapper.setObjectName("contentWrapper")
        content_wrapper.setStyleSheet(
            f"#contentWrapper {{ background: {styles.DARK_BG}; }}")
        content_lay = QVBoxLayout(content_wrapper)
        content_lay.setContentsMargins(30, 25, 30, 25)
        content_lay.setSpacing(16)

        # Header frame
        header = QFrame()
        header.setStyleSheet("background: transparent;")
        header_lay = QVBoxLayout(header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        header_lay.setSpacing(10)

        # Row 1: title + clock
        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        self._title_lbl = QLabel("Επισκόπηση Συστήματος")
        self._title_lbl.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self._title_lbl.setStyleSheet("color: #e0e4ec;")
        title_row.addWidget(self._title_lbl, 1)

        self._clock_lbl = QLabel("")
        self._clock_lbl.setFont(QFont("Courier New", 13))
        self._clock_lbl.setStyleSheet(f"color: {styles.GREEN};")
        title_row.addWidget(self._clock_lbl)

        header_lay.addLayout(title_row)

        # Row 2: AI command bar
        self._ai_cmd_bar = QLineEdit()
        self._ai_cmd_bar.setPlaceholderText(
            "💡 Πείτε στο Encomm AI τι θέλετε να κάνετε...")
        self._ai_cmd_bar.setMinimumHeight(40)
        self._ai_cmd_bar.setStyleSheet(
            f"QLineEdit {{ background: {styles.DARK_SURFACE}; "
            f"border: 1px solid {styles.BORDER_FOCUS}; "
            f"border-radius: 6px; padding: 8px 12px; "
            f"color: {styles.TEXT_PRIMARY}; font-size: 13px; }}"
            "QLineEdit:focus { border-color: #3B82F6; }")
        header_lay.addWidget(self._ai_cmd_bar)

        content_lay.addWidget(header)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setObjectName("pageStack")
        self._stack.setStyleSheet(
            f"#pageStack {{ background: {styles.DARK_BG}; }}")
        self._pages: dict[str, QWidget] = {}
        self._page_indices: dict[str, int] = {}

        for idx, (key, _label) in enumerate(NAV_ITEMS):
            placeholder = QWidget()
            self._stack.addWidget(placeholder)
            self._page_indices[key] = idx

        content_lay.addWidget(self._stack, 1)
        root.addWidget(content_wrapper, 1)

        # ── Status bar ───────────────────────────────────────────────
        self._status_lbl = QLabel("Έτοιμο")
        self._status_lbl.setStyleSheet(
            f"color: {styles.GREEN}; padding: 2px 8px;")
        sb = QStatusBar()
        sb.addPermanentWidget(self._status_lbl)
        self.setStatusBar(sb)

        # ── Start on dashboard + start clock ─────────────────────────
        self.navigate_to("dashboard")
        self._update_clock()
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

    # ── Clock ─────────────────────────────────────────────────────────
    def _update_clock(self):
        now = QDateTime.currentDateTime()
        gr = QLocale(QLocale.Greek)
        self._clock_lbl.setText(
            f"🕒  {gr.toString(now, 'dddd, yyyy-MM-dd HH:mm:ss')}")

    # ── Navigation ────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        """Shut down any active background workers before closing.

        Returns immediately if all pages return True from shutdown().
        Otherwise ignores the event, connects each pending page's
        shutdown_ready signal to a retry, and closes when all are ready."""
        pending: list = []
        for page in self._pages.values():
            if hasattr(page, "shutdown") and not page.shutdown():
                pending.append(page)

        if not pending:
            super().closeEvent(event)
            return

        event.ignore()
        # Avoid duplicate connections on repeated close attempts
        if getattr(self, "_close_retry_armed", False):
            return
        self._close_retry_armed = True

        def _try_close():
            for p in pending:
                if hasattr(p, "_close_pending") and p._close_pending:
                    return  # still waiting
            self._close_retry_armed = False
            self.close()

        for page in pending:
            page.shutdown_ready.connect(_try_close)

    def _on_nav_clicked(self, idx: int) -> None:
        """QButtonGroup slot — translate button index → page key."""
        if 0 <= idx < len(self._nav_keys):
            self.navigate_to(self._nav_keys[idx])

    def navigate_to(self, key: str) -> None:
        """Switch to the named page, creating it lazily if needed."""
        if self._current_page == key:
            return
        self._current_page = key

        # Check the matching sidebar button via QButtonGroup
        try:
            idx = self._nav_keys.index(key)
            btn = self._nav_group.button(idx)
            if btn:
                btn.setChecked(True)
        except ValueError:
            pass

        # Update header title
        self._title_lbl.setText(PAGE_TITLES.get(key, key))

        # Build destination page, switch deterministically
        dest = self._ensure_page(key)
        dest.show()
        dest.raise_()
        dest.updateGeometry()
        self._stack.setCurrentWidget(dest)
        self._stack.update()

        # Update status bar
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
