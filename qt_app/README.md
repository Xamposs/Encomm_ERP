# ENCOMM ERP — Qt Application Shell

PySide6 / Qt presentation layer for ENCOMM ERP.

## Quick start

```powershell
pip install -r requirements.txt
python qt_main.py
```

## Architecture

```
qt_main.py                     # Entry point
qt_app/
    __init__.py                # Package docstring
    main_window.py             # MainWindow (sidebar + stacked pages)
    styles.py                  # Dark-theme palette + QSS stylesheet
    pages/
        __init__.py            # PAGE_CLASSES registry
        base_page.py           # BasePage ABC
        dashboard_page.py      # System overview
        inventory_page.py      # Product inventory
        suppliers_page.py      # Supplier registry
        pos_page.py            # Point of sale
        customers_page.py      # Customer registry
        invoice_history_page.py # Invoice history
        stock_movements_page.py # Stock movements
        settings_page.py       # System settings
        ai_page.py             # AI assistant
```

## How it works

1. `qt_main.py` creates a `QApplication`, applies the dark palette and
   global stylesheet from `qt_app/styles.py`, then shows `MainWindow`.

2. `MainWindow` builds a fixed 220 px sidebar with navigation buttons
   and a `QStackedWidget` for page content.

3. Pages are created **lazily** — a placeholder `QWidget` sits in the
   stack until the user clicks that navigation button, at which point
   the real page class is instantiated and swapped in.

4. Each page inherits from `BasePage`, which receives `db_service` +
   `config` and supports a `build_ui()` / `refresh()` lifecycle.

## Dependencies

- Python 3.11+
- PySide6 6.11.1 (tested) — `pip install PySide6`
