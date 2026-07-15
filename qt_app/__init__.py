"""ENCOMM ERP — PySide6 / Qt Application Shell.

This package is the future presentation layer.  It is currently a
navigation-only shell with placeholder pages.  Business logic, database
access, and AI integration will be wired in during the gradual migration
from the CustomTkinter application.

Package structure:

    qt_app/
        main_window.py    — MainWindow (sidebar + stacked pages + nav)
        styles.py         — Centralised dark-theme palette & QSS
        pages/
            base_page.py              — BasePage ABC
            dashboard_page.py         — Dashboard (στατιστικά)
            inventory_page.py         — Inventory / Stock (αποθήκη)
            suppliers_page.py         — Suppliers (προμηθευτές)
            pos_page.py               — Point of Sale (ταμείο)
            customers_page.py         — Customers (πελάτες)
            invoice_history_page.py   — Invoice history (ιστορικό)
            stock_movements_page.py   — Stock movements (κινήσεις)
            settings_page.py          — Settings (ρυθμίσεις)
            ai_page.py                — AI assistant (AI βοηθός)

Entry point: ``python qt_main.py``.
"""
