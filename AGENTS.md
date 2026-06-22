# ENCOMM ERP — Agent Context

## Project Identity
- **Name:** ENCOMM ERP (Pharmacy Management System)
- **Repo:** https://github.com/Xamposs/Encomm_ERP
- **Stack:** Python 3.11, CustomTkinter, SQLite
- **Entry:** `main.py` → `python main.py`

## Architecture (3-Layer)

```
presentation/     → CustomTkinter GUI (main_window.py — 164KB, be careful)
infrastructure/   → Database, Excel parser, AI service, licensing
core/             → Domain models, business rules, undo stack, intent factory
```

### Data flow
```
GUI (presentation) → Business Rules (core) → Database (infrastructure)
                                         → AI Service (infrastructure)
```

## Key Files

| File | Size | Risk | What it does |
|------|------|:----:|-------------|
| `main.py` | 4KB | Low | App entry, logging, config, DB init |
| `presentation/main_window.py` | 164KB | ⚠️ HIGH | Entire GUI — change carefully |
| `infrastructure/database_service.py` | 46KB | ⚠️ HIGH | All DB operations, 600+ lines |
| `infrastructure/excel_parser_service.py` | 5KB | Medium | Excel import for products/invoices |
| `infrastructure/ai_service.py` | 5.6KB | Medium | AI integration |
| `core/business_rules.py` | 2KB | Low | VAT, expiry, stock checks |
| `core/domain_models.py` | 0.4KB | Low | Product, Invoice dataclasses |
| `core/undo_stack.py` | 2.9KB | Medium | Undo/redo operations |
| `core/intent_factory.py` | 3.6KB | Medium | Intent parsing |

## Database (SQLite — pharmacy.db)

Tables: `ProductMaster`, `suppliers`, `customers`, `invoices`, `invoice_items`, `SystemConfig`

- Schema loaded by `DatabaseService.__init__()` — check `_create_tables` method
- Config persisted in `SystemConfig` table (VAT rate, stock thresholds, expiry alerts)
- WAL journal mode active

## Conventions
- Language: Greek locale (el) — UI is Greek
- Date format: YYYY-MM-DD
- VAT: 15% default (configurable)
- Logging: file + console, format: `%(asctime)s - %(levelname)s - %(message)s`
- Use `patch` tool for targeted edits (NOT full rewrites of large files)

## DO NOT
- Rewrite `main_window.py` from scratch — it's 164KB of working GUI
- Change DB schema without asking
- Delete pharmacy.db without backup
- Modify `main.py` logging/config structure unless necessary

## Running the app
```bash
cd C:/Users/xampos/Desktop/ERP
python main.py
```

## Tests
```bash
# Syntax check before commits
python -m py_compile main.py core/*.py infrastructure/*.py presentation/*.py
```

## Hermes Skills for this project
- `coding-fusion` — use for complex features (5 Qwen workers + Flash judges)
- `erp-db-explorer` — explore database schema and data safely
- `plan` — write a plan before touching critical files
- `systematic-debugging` — root cause analysis for bugs
- `requesting-code-review` — pre-commit quality gates
