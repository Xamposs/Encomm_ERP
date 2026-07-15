"""
PySide6 Resize Smoothness Prototype for ENCOMM ERP
===================================================

Validates whether Qt provides smooth window-resize on Windows with
a widget density comparable to the production CustomTkinter UI.

Run:
  pip install -r prototypes/requirements-qt-prototype.txt
  python prototypes/qt_resize_prototype.py

What to watch:
  - Drag any window edge or corner continuously for 10+ seconds.
  - The sidebar, header, table, and all widgets should remain visible.
  - The FPS counter (status bar, bottom-right) should stay ≥40 fps.
  - No white flash, no content disappearance, no delayed layout.
"""

import sys
import time
from collections import deque
from random import randint, choice

from PySide6.QtCore import Qt, QTimer, QDateTime, QSize
from PySide6.QtGui import QFont, QColor, QPalette, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QStackedWidget, QStatusBar, QSizePolicy, QScrollArea,
    QGridLayout, QComboBox, QSpinBox, QCheckBox, QGroupBox, QSplitter
)

# ---------------------------------------------------------------------------
# Metrics — FPS + resize-event timing
# ---------------------------------------------------------------------------
class PerfMonitor:
    """Tracks paint-event timestamps to compute rolling FPS."""

    def __init__(self, window_size: int = 60):
        self._timestamps: deque[float] = deque(maxlen=window_size)
        self._resize_count = 0
        self._resize_start: float | None = None
        self._max_frame_gap_ms = 0.0
        self._min_fps_ever = 999.0

    def tick(self):
        now = time.perf_counter()
        self._timestamps.append(now)

    @property
    def fps(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        fps_val = (len(self._timestamps) - 1) / elapsed
        if fps_val < self._min_fps_ever and len(self._timestamps) >= 10:
            self._min_fps_ever = fps_val
        return fps_val

    @property
    def max_gap_ms(self) -> float:
        return self._max_frame_gap_ms

    @property
    def min_fps(self) -> float:
        return self._min_fps_ever if self._min_fps_ever < 999.0 else 0.0

    def record_resize_event(self):
        now = time.perf_counter()
        if self._resize_start is not None:
            gap = (now - self._resize_start) * 1000.0
            if gap > self._max_frame_gap_ms:
                self._max_frame_gap_ms = gap
        self._resize_start = now
        self._resize_count += 1

    def stats_text(self) -> str:
        parts = [f"FPS: {self.fps:.0f}"]
        if self.min_fps:
            parts.append(f"min: {self.min_fps:.0f}")
        if self.max_gap_ms:
            parts.append(f"max gap: {self.max_gap_ms:.0f}ms")
        parts.append(f"resize events: {self._resize_count}")
        return "  │  ".join(parts)


# ---------------------------------------------------------------------------
# Dense mock pages — each simulates a real ERP view's widget count
# ---------------------------------------------------------------------------
def _make_table(parent, rows: int, cols: int, headers: list[str]) -> QTableWidget:
    table = QTableWidget(rows, cols, parent)
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().setVisible(False)
    for r in range(rows):
        for c in range(cols):
            val = f"{headers[c][:3]}{r + 1}" if c == 0 else str(randint(1, 999))
            table.setItem(r, c, QTableWidgetItem(val))
    return table


def _h_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


def _stat_card(title: str, value: str, color: str = "#34C759") -> QFrame:
    card = QFrame()
    card.setFrameShape(QFrame.StyledPanel)
    card.setStyleSheet(f"QFrame {{ background: {_dark_surface()}; border-radius: 6px; "
                       f"border: 1px solid #2b303c; padding: 12px; }}")
    lay = QVBoxLayout(card)
    lay.setSpacing(4)
    lbl_val = QLabel(value)
    lbl_val.setFont(QFont("Segoe UI", 22, QFont.Bold))
    lbl_val.setStyleSheet(f"color: {color};")
    lbl_title = QLabel(title)
    lbl_title.setStyleSheet("color: #8a8f98; font-size: 11px;")
    lay.addWidget(lbl_val)
    lay.addWidget(lbl_title)
    return card


def _dark_surface() -> str:
    return "#1A1D24"


def _dark_bg() -> str:
    return "#0F1117"


def _accent() -> str:
    return "#3B82F6"


# ── Dashboard page (simulates the real ERP dashboard) ──
def make_dashboard_page() -> QWidget:
    page = QWidget()
    main_lay = QVBoxLayout(page)
    main_lay.setContentsMargins(30, 25, 30, 25)
    main_lay.setSpacing(16)

    # Row of stat cards
    cards = QHBoxLayout()
    cards.setSpacing(12)
    cards.addWidget(_stat_card("Προϊόντα σε απόθεμα", "2,847"))
    cards.addWidget(_stat_card("Χαμηλό απόθεμα", "12", "#F59E0B"))
    cards.addWidget(_stat_card("Πωλήσεις σήμερα", "€4,230"))
    cards.addWidget(_stat_card("Ληγμένα", "3", "#EF4444"))
    main_lay.addLayout(cards)

    main_lay.addWidget(_h_line())

    # Activity table
    lbl = QLabel("📋 Πρόσφατη Δραστηριότητα")
    lbl.setFont(QFont("Segoe UI", 13, QFont.Bold))
    lbl.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(lbl)
    table = _make_table(page, 8, 4, ["Ημερομηνία", "Τύπος", "Περιγραφή", "Ποσό"])
    main_lay.addWidget(table, 1)

    return page


# ── Inventory page (simulates the real inventory view) ──
def make_inventory_page() -> QWidget:
    page = QWidget()
    main_lay = QVBoxLayout(page)
    main_lay.setContentsMargins(30, 20, 30, 20)
    main_lay.setSpacing(12)

    # Search + filters bar
    bar = QHBoxLayout()
    search = QLineEdit()
    search.setPlaceholderText("🔍 Αναζήτηση προϊόντος...")
    search.setMinimumHeight(36)
    bar.addWidget(search, 2)
    cat = QComboBox()
    cat.addItems(["Όλες οι κατηγορίες", "Φάρμακα", "Καλλυντικά", "Συμπληρώματα"])
    bar.addWidget(cat, 1)
    stock = QComboBox()
    stock.addItems(["Όλα", "Μόνο ελλείψεις", "Κοντά στη λήξη"])
    bar.addWidget(stock, 1)
    add_btn = QPushButton("➕ Προσθήκη")
    add_btn.setStyleSheet(f"QPushButton {{ background: {_accent()}; color: white; "
                          f"border-radius: 6px; padding: 8px 16px; font-weight: bold; }}")
    bar.addWidget(add_btn)
    main_lay.addLayout(bar)

    # Table
    headers = ["Barcode", "Περιγραφή", "Κατηγορία", "Τιμή", "Stock", "Ημ/νία Λήξης"]
    table = _make_table(page, 14, len(headers), headers)
    main_lay.addWidget(table, 1)

    # Bottom bar
    bot = QHBoxLayout()
    pg_lbl = QLabel("Σελίδα 1 από 12  ·  142 προϊόντα")
    pg_lbl.setStyleSheet("color: #8a8f98;")
    bot.addWidget(pg_lbl)
    bot.addStretch()
    for txt in ("◀", "1", "2", "3", "▶"):
        btn = QPushButton(txt)
        btn.setFixedSize(32, 32)
        bot.addWidget(btn)
    main_lay.addLayout(bot)

    return page


# ── Settings page (dense form) ──
def make_settings_page() -> QWidget:
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)

    inner = QWidget()
    lay = QGridLayout(inner)
    lay.setContentsMargins(30, 20, 30, 20)
    lay.setSpacing(14)

    group = QGroupBox("⚙️ Γενικές Ρυθμίσεις")
    g_lay = QGridLayout(group)
    g_lay.setSpacing(8)
    rows = [
        ("Ποσοστό ΦΠΑ (%)", QSpinBox, {"value": 15, "range": (0, 27)}),
        ("Όριο χαμηλού αποθέματος", QSpinBox, {"value": 10, "range": (0, 999)}),
        ("Ειδοποίηση λήξης (ημέρες)", QSpinBox, {"value": 30, "range": (0, 365)}),
        ("Γλώσσα", QComboBox, {"items": ["Ελληνικά", "English"]}),
        ("Θέμα", QComboBox, {"items": ["Dark", "Light"]}),
        ("Αυτόματο backup", QCheckBox, {"checked": True}),
        ("API Key", QLineEdit, {"placeholder": "sk-...", "echo": QLineEdit.Password}),
    ]
    for i, (label, cls, kwargs) in enumerate(rows):
        g_lay.addWidget(QLabel(label), i, 0)
        w = cls()
        if isinstance(w, QSpinBox):
            w.setValue(kwargs.get("value", 0))
            w.setRange(*kwargs.get("range", (0, 100)))
        elif isinstance(w, QComboBox):
            w.addItems(kwargs.get("items", []))
        elif isinstance(w, QCheckBox):
            w.setChecked(kwargs.get("checked", False))
        elif isinstance(w, QLineEdit):
            if "placeholder" in kwargs:
                w.setPlaceholderText(kwargs["placeholder"])
            if "echo" in kwargs:
                w.setEchoMode(kwargs["echo"])
        g_lay.addWidget(w, i, 1)

    lay.addWidget(group, 0, 0)
    scroll.setWidget(inner)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)
    return page


# ── POS page ──
def make_pos_page() -> QWidget:
    page = QWidget()
    h = QHBoxLayout(page)
    h.setContentsMargins(20, 20, 20, 20)
    h.setSpacing(16)

    # Left: cart
    left = QVBoxLayout()
    left.addWidget(QLabel("🧾 Καλάθι"))
    cart_table = _make_table(None, 6, 3, ["Προϊόν", "Ποσότητα", "Τιμή"])
    left.addWidget(cart_table, 1)
    total = QLabel("Σύνολο: €0.00")
    total.setFont(QFont("Segoe UI", 16, QFont.Bold))
    total.setStyleSheet("color: #34C759;")
    left.addWidget(total)
    h.addLayout(left, 3)

    # Right: product grid (simulates many buttons)
    right = QVBoxLayout()
    right.addWidget(QLabel("📦 Προϊόντα"))
    grid = QGridLayout()
    grid.setSpacing(4)
    prods = ["Depon", "Algofren", "Panadol", "Aspirin", "Nurofen",
             "Aerius", "Zyrtec", "Xozal", "Claritin", "Moment",
             "Buscapina", "Imodium", "Maalox", "Gaviscon", "Lasix"]
    for i, name in enumerate(prods):
        btn = QPushButton(name)
        btn.setMinimumHeight(48)
        grid.addWidget(btn, i // 3, i % 3)
    right.addLayout(grid)
    right.addStretch()
    h.addLayout(right, 2)

    return page


# ---------------------------------------------------------------------------
# Main Window — mirrors the ERP shell layout
# ---------------------------------------------------------------------------
class PrototypeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._perf = PerfMonitor()
        self._clock_timer: QTimer | None = None

        self.setWindowTitle("ENCOMM ERP — PySide6 Resize Prototype")
        self.resize(1150, 730)
        self.setMinimumSize(1050, 650)
        self._apply_dark_palette()

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(f"QFrame {{ background: {_dark_surface()}; }}")
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(16, 30, 16, 20)
        side_lay.setSpacing(6)

        brand = QLabel("ENCOMM ERP 🧪")
        brand.setFont(QFont("Segoe UI", 18, QFont.Bold))
        brand.setStyleSheet("color: #d0d4dc; padding-bottom: 12px;")
        side_lay.addWidget(brand)

        self._nav_btns: dict[str, QPushButton] = {}
        nav_items = [
            ("dashboard",   "📊  Αρχική"),
            ("inventory",   "📦  Αποθήκη"),
            ("suppliers",   "🏭  Προμηθευτές"),
            ("pos",         "🧾  Ταμείο / Πωλήσεις"),
            ("customers",   "👥  Πελάτες"),
            ("history",     "🔎  Ιστορικό"),
            ("movements",   "📋  Κινήσεις"),
            ("settings",    "⚙️  Ρυθμίσεις"),
            ("ai",          "🤖  AI Βοηθός"),
        ]
        for key, label in nav_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(self._nav_btn_style(active=False))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self._switch_page(k))
            side_lay.addWidget(btn)
            self._nav_btns[key] = btn

        side_lay.addStretch()
        ver = QLabel("v1.0.0 Stable | ENCOMM Tensor Intelligence")
        ver.setStyleSheet("color: #5a5f6a; font-size: 10px;")
        side_lay.addWidget(ver)

        root.addWidget(sidebar)

        # ── Main content ──
        right = QVBoxLayout()
        right.setContentsMargins(30, 25, 30, 25)
        right.setSpacing(16)

        self._title_lbl = QLabel("Επισκόπηση Συστήματος")
        self._title_lbl.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self._title_lbl.setStyleSheet("color: #e0e4ec;")
        right.addWidget(self._title_lbl)

        self._ai_bar = QLineEdit()
        self._ai_bar.setPlaceholderText(
            "💡 Πείτε στο Encomm AI τι θέλετε να κάνετε...")
        self._ai_bar.setMinimumHeight(40)
        self._ai_bar.setStyleSheet(
            f"QLineEdit {{ background: {_dark_surface()}; border: 1px solid #3B5068; "
            f"border-radius: 6px; padding: 6px 12px; color: #c0c8d4; }}")
        right.addWidget(self._ai_bar)

        self._stack = QStackedWidget()
        self._stack.addWidget(make_dashboard_page())   # index 0
        self._stack.addWidget(make_inventory_page())   # index 1
        self._stack.addWidget(make_settings_page())    # index 2
        self._stack.addWidget(make_pos_page())         # index 3
        right.addWidget(self._stack, 1)

        content_wrapper = QWidget()
        content_wrapper.setLayout(right)
        root.addWidget(content_wrapper, 1)

        # ── Status bar with FPS ──
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #34C759; padding: 2px 8px;")
        sb = QStatusBar()
        sb.addPermanentWidget(self._status_lbl)
        self.setStatusBar(sb)

        # ── Timers ──
        # Paint-event ticker: drive perf monitor at display refresh rate
        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self._perf.tick)
        self._paint_timer.start(16)  # ~60 Hz

        # Status-bar refresh
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(250)

        # Start on dashboard
        self._switch_page("dashboard")

    # ── Resize-tracking via resizeEvent override ──
    def resizeEvent(self, event):
        self._perf.record_resize_event()
        super().resizeEvent(event)

    # ── Page switching ──
    def _switch_page(self, key: str):
        titles = {
            "dashboard": "Επισκόπηση Συστήματος",
            "inventory": "Διαχείριση Αποθήκης",
            "suppliers": "Μητρώο Προμηθευτών",
            "pos": "Ταμείο / Πωλήσεις (POS)",
            "customers": "Μητρώο Πελατών",
            "history": "Ιστορικό Παραστατικών",
            "movements": "Κινήσεις Αποθέματος",
            "settings": "Ρυθμίσεις Συστήματος",
            "ai": "AI Βοηθός",
        }
        page_map = {
            "dashboard": 0, "inventory": 1, "settings": 2, "pos": 3,
            "suppliers": 2, "customers": 2, "history": 2,
            "movements": 2, "ai": 2,  # fall back to settings page
        }
        self._title_lbl.setText(titles.get(key, key))
        idx = page_map.get(key, 0)
        if idx < self._stack.count():
            self._stack.setCurrentIndex(idx)

        for k, btn in self._nav_btns.items():
            active = (k == key)
            btn.setChecked(active)
            btn.setStyleSheet(self._nav_btn_style(active=active))

    def _nav_btn_style(self, active: bool) -> str:
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

    def _update_status(self):
        self._status_lbl.setText(self._perf.stats_text())

    @staticmethod
    def _apply_dark_palette():
        app = QApplication.instance()
        if app is None:
            return
        p = app.palette()
        p.setColor(QPalette.Window,         QColor(_dark_bg()))
        p.setColor(QPalette.WindowText,     QColor("#d0d4dc"))
        p.setColor(QPalette.Base,           QColor(_dark_surface()))
        p.setColor(QPalette.AlternateBase,  QColor("#22252C"))
        p.setColor(QPalette.Text,           QColor("#d0d4dc"))
        p.setColor(QPalette.Button,         QColor("#252b36"))
        p.setColor(QPalette.ButtonText,     QColor("#d0d4dc"))
        p.setColor(QPalette.Highlight,      QColor(_accent()))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        app.setPalette(p)
        app.setStyleSheet(
            f"QMainWindow {{ background: {_dark_bg()}; }}\n"
            f"QTableWidget {{ background: {_dark_surface()}; "
            f"  alternate-background-color: #22252C; "
            f"  gridline-color: #2b303c; border: 1px solid #2b303c; "
            f"  border-radius: 6px; }}\n"
            f"QHeaderView::section {{ background: #252b36; color: #b0b8c4; "
            f"  border: none; padding: 6px; font-weight: bold; }}\n"
            f"QScrollBar:vertical {{ background: {_dark_surface()}; width: 8px; }}\n"
            f"QScrollBar::handle:vertical {{ background: #3b3f48; "
            f"  border-radius: 4px; min-height: 20px; }}\n"
        )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ENCOMM ERP Qt Prototype")
    win = PrototypeWindow()
    win.show()
    sys.exit(app.exec())
