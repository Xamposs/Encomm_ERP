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
  - All sidebar buttons, header, tables, and widgets should remain visible.
  - The UI pulse-rate counter (status bar, bottom-right) should stay ≥50 Hz.
  - No white flash, no content disappearance, no delayed layout.
"""

import sys
import time
from collections import deque
from random import randint

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QStatusBar, QScrollArea,
    QGridLayout, QComboBox, QSpinBox, QCheckBox, QGroupBox
)

# ---------------------------------------------------------------------------
# Metrics — UI pulse rate + resize-event timing
# ---------------------------------------------------------------------------
class PerfMonitor:
    """Tracks timer-tick timestamps to compute rolling UI pulse rate.

    This is NOT rendered-frame FPS (no swap-buffer / vsync hook).
    It measures how regularly the Qt event loop dispatches a high-frequency
    QTimer — an *event-loop responsiveness* signal."""

    def __init__(self, window_size: int = 60):
        self._timestamps: deque[float] = deque(maxlen=window_size)
        self._resize_count = 0
        self._resize_start: float | None = None
        self._max_frame_gap_ms = 0.0
        self._min_pulse_ever = 999.0

    def tick(self):
        now = time.perf_counter()
        self._timestamps.append(now)

    @property
    def pulse_rate(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        rate_val = (len(self._timestamps) - 1) / elapsed
        if rate_val < self._min_pulse_ever and len(self._timestamps) >= 10:
            self._min_pulse_ever = rate_val
        return rate_val

    @property
    def max_gap_ms(self) -> float:
        return self._max_frame_gap_ms

    @property
    def min_pulse_rate(self) -> float:
        return self._min_pulse_ever if self._min_pulse_ever < 999.0 else 0.0

    def record_resize_event(self):
        now = time.perf_counter()
        if self._resize_start is not None:
            gap = (now - self._resize_start) * 1000.0
            if gap > self._max_frame_gap_ms:
                self._max_frame_gap_ms = gap
        self._resize_start = now
        self._resize_count += 1

    def stats_text(self) -> str:
        parts = [f"UI pulse: {self.pulse_rate:.0f} Hz"]
        if self.min_pulse_rate:
            parts.append(f"min: {self.min_pulse_rate:.0f}")
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


# ── Dense ERP Dashboard — all 4 page widgets packed into one scrollable layout ──
def make_dense_dashboard() -> QScrollArea:
    """One single page with every widget from Dashboard, Inventory, Settings,
    and POS regions.  Packed into a QScrollArea so the window can be resized
    smaller than the content and the user still sees everything.
    Approximate widget count: ~90–110."""
    outer = QScrollArea()
    outer.setWidgetResizable(True)
    outer.setFrameShape(QFrame.NoFrame)

    page = QWidget()
    main_lay = QVBoxLayout(page)
    main_lay.setContentsMargins(30, 20, 30, 20)
    main_lay.setSpacing(16)

    # ── Region: Dashboard ──
    region_lbl = QLabel("📊  Στατιστικά (Dashboard)")
    region_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
    region_lbl.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(region_lbl)

    cards = QHBoxLayout()
    cards.setSpacing(12)
    cards.addWidget(_stat_card("Προϊόντα σε απόθεμα", "2,847"))
    cards.addWidget(_stat_card("Χαμηλό απόθεμα", "12", "#F59E0B"))
    cards.addWidget(_stat_card("Πωλήσεις σήμερα", "€4,230"))
    cards.addWidget(_stat_card("Ληγμένα", "3", "#EF4444"))
    main_lay.addLayout(cards)
    main_lay.addWidget(_h_line())

    activity_lbl = QLabel("Πρόσφατη Δραστηριότητα")
    activity_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
    activity_lbl.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(activity_lbl)
    main_lay.addWidget(_make_table(page, 8, 4,
        ["Ημερομηνία", "Τύπος", "Περιγραφή", "Ποσό"]))
    main_lay.addWidget(_h_line())

    # ── Region: Inventory ──
    region_lbl2 = QLabel("📦  Αποθήκη (Inventory)")
    region_lbl2.setFont(QFont("Segoe UI", 14, QFont.Bold))
    region_lbl2.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(region_lbl2)

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

    headers = ["Barcode", "Περιγραφή", "Κατηγορία", "Τιμή", "Stock", "Ημ/νία Λήξης"]
    main_lay.addWidget(_make_table(page, 14, len(headers), headers))

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
    main_lay.addWidget(_h_line())

    # ── Region: POS ──
    region_lbl3 = QLabel("🧾  Ταμείο / Πωλήσεις (POS)")
    region_lbl3.setFont(QFont("Segoe UI", 14, QFont.Bold))
    region_lbl3.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(region_lbl3)

    pos_row = QHBoxLayout()
    pos_row.setSpacing(16)

    left = QVBoxLayout()
    left.addWidget(QLabel("🧾 Καλάθι"))
    cart_table = _make_table(None, 6, 3, ["Προϊόν", "Ποσότητα", "Τιμή"])
    left.addWidget(cart_table, 1)
    total = QLabel("Σύνολο: €0.00")
    total.setFont(QFont("Segoe UI", 16, QFont.Bold))
    total.setStyleSheet("color: #34C759;")
    left.addWidget(total)
    pos_row.addLayout(left, 3)

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
    pos_row.addLayout(right, 2)
    main_lay.addLayout(pos_row)
    main_lay.addWidget(_h_line())

    # ── Region: Settings ──
    region_lbl4 = QLabel("⚙️  Ρυθμίσεις (Settings)")
    region_lbl4.setFont(QFont("Segoe UI", 14, QFont.Bold))
    region_lbl4.setStyleSheet("color: #d0d4dc;")
    main_lay.addWidget(region_lbl4)

    group = QGroupBox("Γενικές Ρυθμίσεις")
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
    main_lay.addWidget(group)

    main_lay.addStretch()
    outer.setWidget(page)
    return outer


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

        # Display-only nav buttons (visual density, not functional)
        nav_items = [
            "📊  Αρχική",
            "📦  Αποθήκη",
            "🏭  Προμηθευτές",
            "🧾  Ταμείο / Πωλήσεις",
            "👥  Πελάτες",
            "🔎  Ιστορικό",
            "📋  Κινήσεις",
            "⚙️  Ρυθμίσεις",
            "🤖  AI Βοηθός",
        ]
        for label in nav_items:
            btn = QPushButton(label)
            btn.setStyleSheet(self._nav_btn_style())
            side_lay.addWidget(btn)

        side_lay.addStretch()
        ver = QLabel("v1.0.0 Stable | ENCOMM Tensor Intelligence")
        ver.setStyleSheet("color: #5a5f6a; font-size: 10px;")
        side_lay.addWidget(ver)

        root.addWidget(sidebar)

        # ── Main content ──
        right = QVBoxLayout()
        right.setContentsMargins(30, 25, 30, 25)
        right.setSpacing(12)

        self._ai_bar = QLineEdit()
        self._ai_bar.setPlaceholderText(
            "💡 Πείτε στο Encomm AI τι θέλετε να κάνετε...")
        self._ai_bar.setMinimumHeight(40)
        self._ai_bar.setStyleSheet(
            f"QLineEdit {{ background: {_dark_surface()}; border: 1px solid #3B5068; "
            f"border-radius: 6px; padding: 6px 12px; color: #c0c8d4; }}")
        right.addWidget(self._ai_bar)

        # Single dense scrollable dashboard — all 4 regions in one page
        right.addWidget(make_dense_dashboard(), 1)

        content_wrapper = QWidget()
        content_wrapper.setLayout(right)
        root.addWidget(content_wrapper, 1)

        # ── Status bar with UI pulse rate ──
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #34C759; padding: 2px 8px;")
        sb = QStatusBar()
        sb.addPermanentWidget(self._status_lbl)
        self.setStatusBar(sb)

        # ── Timers ──
        # UI pulse ticker: samples event-loop responsiveness at ~60 Hz
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._perf.tick)
        self._pulse_timer.start(16)

        # Status-bar refresh
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(250)

    # ── Resize-tracking via resizeEvent override ──
    def resizeEvent(self, event):
        self._perf.record_resize_event()
        super().resizeEvent(event)

    @staticmethod
    def _nav_btn_style() -> str:
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
