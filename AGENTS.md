# ENCOMM ERP — Agent Context

## Project Identity
- **Name:** ENCOMM ERP (Pharmacy Management System)
- **Repo:** https://github.com/Xamposs/Encomm_ERP
- **Stack:** Python 3.11, CustomTkinter, SQLite
- **Entry:** `main.py` → `python main.py`

## Architecture (3-Layer)

```
presentation/     → CustomTkinter GUI (main_window.py shell + presentation/views/* modules)
infrastructure/   → Database, Excel parser, AI service, licensing
core/             → Domain models, business rules, undo stack, intent factory
tests/            → pytest suite (business_rules, database_service, undo_stack, intent_factory)
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
| `presentation/main_window.py` | ~30KB | Medium | GUI shell + view wiring (views live in `presentation/views/`) |
| `presentation/views/` | ~10 files | Medium | One `BaseView` subclass per screen (dashboard, inventory, POS, etc.) |
| `infrastructure/database_service.py` | ~80KB | ⚠️ HIGH | All DB operations, ~1900 lines |
| `infrastructure/excel_parser_service.py` | 6KB | Medium | Excel import for products/invoices |
| `infrastructure/ai_service.py` | 6.5KB | Medium | AI integration |
| `core/business_rules.py` | 4.7KB | Low | VAT, expiry, stock checks, EAN-13 |
| `core/domain_models.py` | 1.3KB | Low | Product, Supplier, Invoice dataclasses |
| `core/undo_stack.py` | 3.8KB | Medium | Undo/redo operations |
| `core/intent_factory.py` | 4KB | Medium | Intent parsing |
| `tests/` | — | Low | pytest suite |

## Database (SQLite — encomm_erp.db)

Tables: `ProductMaster`, `suppliers`, `customers`, `invoices`, `invoice_items`, `stock_movements`, `SystemConfig`

- Schema loaded by `DatabaseService.__init__()` — check `_initialize_db` method
- Config persisted in `SystemConfig` table (VAT rate, stock thresholds, expiry alerts)
- WAL journal mode active (set once at init, not per-connection)
- New installs get CHECK constraints (`Stock >= 0`, `Price >= 0`) and FKs

## Conventions
- Language: Greek locale (el) — UI is Greek
- Date format: YYYY-MM-DD
- VAT: 15% default (configurable)
- Logging: file + console, format: `%(asctime)s - %(levelname)s - %(message)s`
- Use `patch` tool for targeted edits (NOT full rewrites of large files)

## DO NOT
- Rewrite `main_window.py` from scratch — it is a thin shell; GUI logic lives in `presentation/views/`
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
python -m py_compile main.py core/*.py infrastructure/*.py presentation/*.py presentation/views/*.py

# Unit + integration tests (DB layer, business rules, undo, intents)
pytest -q
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
