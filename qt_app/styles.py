"""
Centralised dark-theme palette and QSS stylesheet for the ENCOMM ERP
PySide6 application.

Usage
-----
    from qt_app.styles import DARK_PALETTE, GLOBAL_QSS

    app.setPalette(DARK_PALETTE)
    app.setStyleSheet(GLOBAL_QSS)
"""

from PySide6.QtGui import QPalette, QColor

# ── Core palette colours ────────────────────────────────────────────────
DARK_BG      = "#0F1117"
DARK_SURFACE = "#1A1D24"
SURFACE_ALT  = "#22252C"
ACCENT       = "#3B82F6"
ACCENT_HOVER = "#2563EB"
TEXT_PRIMARY = "#d0d4dc"
TEXT_MUTED   = "#8a8f98"
TEXT_DIM     = "#5a5f6a"
BORDER       = "#2b303c"
BORDER_FOCUS = "#3B5068"
GREEN        = "#34C759"
AMBER        = "#F59E0B"
RED          = "#EF4444"
BUTTON_BG    = "#252b36"
SCROLL_HANDLE = "#3b3f48"

# ── QPalette ────────────────────────────────────────────────────────────
DARK_PALETTE = QPalette()
DARK_PALETTE.setColor(QPalette.Window,          QColor(DARK_BG))
DARK_PALETTE.setColor(QPalette.WindowText,      QColor(TEXT_PRIMARY))
DARK_PALETTE.setColor(QPalette.Base,            QColor(DARK_SURFACE))
DARK_PALETTE.setColor(QPalette.AlternateBase,   QColor(SURFACE_ALT))
DARK_PALETTE.setColor(QPalette.Text,            QColor(TEXT_PRIMARY))
DARK_PALETTE.setColor(QPalette.Button,          QColor(BUTTON_BG))
DARK_PALETTE.setColor(QPalette.ButtonText,      QColor(TEXT_PRIMARY))
DARK_PALETTE.setColor(QPalette.Highlight,       QColor(ACCENT))
DARK_PALETTE.setColor(QPalette.HighlightedText, QColor("#ffffff"))

# ── Global QSS stylesheet ───────────────────────────────────────────────
GLOBAL_QSS = f"""
QMainWindow {{
    background: {DARK_BG};
}}

/* ── Tables ── */
QTableWidget {{
    background: {DARK_SURFACE};
    alternate-background-color: {SURFACE_ALT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-size: 13px;
}}
QHeaderView::section {{
    background: {BUTTON_BG};
    color: {TEXT_PRIMARY};
    border: none;
    padding: 6px 10px;
    font-weight: bold;
    font-size: 13px;
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: {DARK_SURFACE};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {SCROLL_HANDLE};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {DARK_SURFACE};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {SCROLL_HANDLE};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Inputs ── */
QLineEdit {{
    background: {DARK_SURFACE};
    border: 1px solid {BORDER_FOCUS};
    border-radius: 6px;
    padding: 8px 12px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}

/* ── Combo boxes ── */
QComboBox {{
    background: {DARK_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {DARK_SURFACE};
    color: {TEXT_PRIMARY};
    selection-background-color: {BUTTON_BG};
    border: 1px solid {BORDER};
}}

/* ── Group boxes ── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 16px;
    padding: 16px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}

/* ── Push buttons ── */
QPushButton {{
    background: {BUTTON_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 13px;
}}
QPushButton:hover {{
    background: {SURFACE_ALT};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: {BORDER};
}}

/* ── Labels ── */
QLabel {{
    color: {TEXT_PRIMARY};
}}

/* ── Status bar ── */
QStatusBar {{
    background: {DARK_SURFACE};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}
"""
