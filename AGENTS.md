# ENCOMM ERP — Agent Context

## Project Identity
- **Name:** ENCOMM ERP (Pharmacy Management System)
- **Repo:** https://github.com/Xamposs/Encomm_ERP
- **Stack:** Python 3.11, PySide6 (Qt), SQLite
- **Entry:** `qt_main.py` → `python qt_main.py`

## Architecture

```
qt_main.py        → PySide6 entry point (QApplication + MainWindow)
qt_app/           → Qt UI layer (main window, pages, styles, data source)
infrastructure/   → Database, Excel parser, AI service, licensing, command services
core/             → Domain models, business rules, undo stack, intent factory
tests/            → pytest suite (business rules, database, Qt pages, commands)
```

### Data flow
```
Qt UI (qt_app) → Business Rules (core) → Database (infrastructure)
                                       → AI Service (infrastructure)
```

## Key Files

| File | Risk | What it does |
|------|:----:|-------------|
| `qt_main.py` | Low | App entry: QApplication + MainWindow factory + `main()` |
| `qt_app/main_window.py` | Medium | Sidebar + QStackedWidget + lazy pages + AI input bar (placeholder) |
| `qt_app/pages/` | Medium | 10 page subclasses (Dashboard, Inventory, POS, etc.) |
| `qt_app/styles.py` | Low | Dark palette + global QSS stylesheet |
| `qt_app/data_source.py` | Medium | Read-only SQLite queries for Qt pages |
| `infrastructure/database_service.py` | ⚠️ HIGH | All DB operations, schema init, migrations |
| `infrastructure/inventory_command_service.py` | Medium | Write commands for product CRUD from Qt |
| `infrastructure/excel_parser_service.py` | Medium | Excel import for products/invoices |
| `infrastructure/ai_service.py` | Medium | AI service layer (planned — not wired into end-user automation) |
| `core/business_rules.py` | Low | Expiry, stock checks, EAN-13 |
| `core/domain_models.py` | Low | Product, Supplier, Invoice dataclasses |
| `core/undo_stack.py` | Low | Standalone domain utility for undo/redo (not currently wired into Qt UI) |
| `core/intent_factory.py` | Low | Intent parsing (planned — not wired into end-user automation) |

## Database (SQLite — encomm_erp.db)

Tables: `ProductMaster`, `suppliers`, `customers`, `invoices`, `invoice_items`, `stock_movements`, `goods_receipts`, `goods_receipt_items`, `stock_lots`, `SystemConfig`

- Schema loaded by `DatabaseService.__init__()` — initialized on first run
- Config persisted in `SystemConfig` table (stock thresholds, expiry alerts)
- WAL journal mode active (set once at init, not per-connection)
- New installs get CHECK constraints (`Stock >= 0`, `Price >= 0`) and FKs
- **DO NOT change DB schema without asking**

## VAT Policy — FROZEN

VAT implementation is frozen and out of scope. Do not modify VAT calculations, defaults, schema, configuration, imports, or tests unless the user explicitly authorizes it.

## Conventions
- Language: Greek locale (el) — UI is Greek
- Date format: YYYY-MM-DD
- Logging: file + console, format: `%(asctime)s - %(levelname)s - %(message)s`
- Use `patch` tool for targeted edits (NOT full rewrites of large files)

## AI Integration — Planned, Not Active

AI integration is planned and must later use explicit intent/policy/approval/audit boundaries. The `ai_service.py` layer and `intent_factory.py` exist as infrastructure scaffolding but are **not** wired into end-user automation. Do not present them as implemented features.

## Running the app
```bash
cd C:/Users/xampos/Desktop/ERP
python qt_main.py
```

## Tests
```bash
# Syntax / import check before commits
python -m compileall -q core infrastructure qt_app

# Full test suite (off-screen Qt)
python -m pytest -q
```

## Hermes Skills for this project
- `coding-fusion` — use for complex features (5 Qwen workers + Flash judges)
- `erp-db-explorer` — explore database schema and data safely
- `plan` — write a plan before touching critical files
- `systematic-debugging` — root cause analysis for bugs
- `requesting-code-review` — pre-commit quality gates
