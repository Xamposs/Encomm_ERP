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
| `qt_app/main_window.py` | Medium | Sidebar + QStackedWidget + lazy pages + AI command bar |
| `qt_app/pages/` | Medium | 10 page subclasses (Dashboard, Inventory, POS, etc.) |
| `qt_app/styles.py` | Low | Dark palette + global QSS stylesheet |
| `qt_app/data_source.py` | Medium | Read-only SQLite queries for Qt pages |
| `infrastructure/database_service.py` | ⚠️ HIGH | All DB operations, schema init, migrations |
| `infrastructure/inventory_command_service.py` | Medium | Write commands for product CRUD from Qt |
| `infrastructure/excel_parser_service.py` | Medium | Excel import for products/invoices |
| `infrastructure/ai_service.py` | Medium | AI integration |
| `core/business_rules.py` | Low | VAT, expiry, stock checks, EAN-13 |
| `core/domain_models.py` | Low | Product, Supplier, Invoice dataclasses |
| `core/undo_stack.py` | Medium | Undo/redo operations |
| `core/intent_factory.py` | Medium | Intent parsing |

## Database (SQLite — encomm_erp.db)

Tables: `ProductMaster`, `suppliers`, `customers`, `invoices`, `invoice_items`, `stock_movements`, `goods_receipts`, `goods_receipt_items`, `stock_lots`, `SystemConfig`

- Schema loaded by `DatabaseService.__init__()` — initialized on first run
- Config persisted in `SystemConfig` table (VAT rate, stock thresholds, expiry alerts)
- WAL journal mode active (set once at init, not per-connection)
- New installs get CHECK constraints (`Stock >= 0`, `Price >= 0`) and FKs
- **DO NOT change DB schema without asking**

## Conventions
- Language: Greek locale (el) — UI is Greek
- Date format: YYYY-MM-DD
- VAT: 15% default (configurable)
- Logging: file + console, format: `%(asctime)s - %(levelname)s - %(message)s`
- Use `patch` tool for targeted edits (NOT full rewrites of large files)

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
