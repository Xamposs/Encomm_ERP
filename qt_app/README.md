# ENCOMM ERP — Qt Application Shell

PySide6 / Qt presentation layer for ENCOMM ERP.

## Status

**Navigation-only shell.** All 9 pages exist as placeholder pages that
display their Greek title and a "ready for migration" message.
Sidebar navigation is fully functional — clicking any button switches
to the corresponding page.

## Quick start

```powershell
pip install -r requirements-qt.txt
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

2. `MainWindow` builds a fixed 220 px sidebar with 9 Greek navigation
   buttons and a `QStackedWidget` for page content.

3. Pages are created **lazily** — the sidebar placeholder is a bare
   `QWidget` until the user clicks that navigation button, at which
   point the real page class is instantiated and swapped in.

4. Each page inherits from `BasePage`, which:
   - Receives `db_service` + `config` (ready for future wiring).
   - Puts a title label at the top.
   - Calls `build_ui()` — currently the default placeholder, but each
     subclass can override it to create real widgets.

## Migration plan

| Phase | What |
|-------|------|
| 1. Shell (current) | Sidebar + stacked pages + navigation |
| 2. Dashboard | Wire real DB stats into `DashboardPage.build_ui()` |
| 3. Inventory | Port the inventory table, search, and filters |
| 4. Suppliers | Port supplier CRUD |
| 5. POS | Port the POS panel |
| 6. Customers | Port customer registry |
| 7. Invoice history | Port historical invoice browser |
| 8. Stock movements | Port movement log |
| 9. Settings | Port settings form (VAT, thresholds, theme, backup) |
| 10. AI | Wire `AIService` and `IntentFactory` |

Each phase is independently testable and does not block the existing
CustomTkinter application.

## Dependencies

- Python 3.11+
- PySide6 6.11.1 (tested) — `pip install PySide6`

## Notes

- The existing CustomTkinter application (`python main.py`) is **not**
  modified by this package and remains the production entry point.
- The Qt application loads no real database — `db_service` is `None`
  and all pages are display-only.
