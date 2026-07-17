"""ENCOMM ERP — PySide6 / Qt Application Shell.

This package is the active presentation layer for ENCOMM ERP.
All UI is delivered through PySide6 / Qt; business logic, database
access, and AI integration are shared with the core and infrastructure
layers.

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
