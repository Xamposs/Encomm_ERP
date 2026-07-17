# POS Phase B1 — Transaction Readiness Audit

> **Status**: Read-only analysis. No code modified. No database created.
> **Date**: 2026-07-16
> **Commit baseline**: `c994581` (current `main`)
> **Authoritative sources**: repository files, not assumptions.

---

## 1. Database Authority

### Configured database path

- `qt_main.py:31` — `config["db_path"] = os.getenv("DB_PATH", "encomm_erp.db")`
- **Production default**: `encomm_erp.db` in the repository root
- No WAL-checkpoint or multi-file database sharding exists for the POS path.

### Canonical write helper

- `infrastructure/database_service.py:31` — `DatabaseService._get_connection()` returns a plain `sqlite3.connect(self.db_path)` with `row_factory`, `PRAGMA foreign_keys=ON`, `synchronous=NORMAL`, `cache_size=-64000`
- **Qt app deliberately avoids DatabaseService** — all Qt data access uses `qt_app/data_source.py:65` `_connect_ro()` which opens `mode=ro` via URI, write-protected at the connection level

### Write command boundary (Qt-compatible)

- `infrastructure/inventory_command_service.py:176` — `create_product()` and `update_product()` use:
  - `sqlite3.connect(db_path)` (plain write connection)
  - `PRAGMA foreign_keys = ON`
  - `BEGIN IMMEDIATE`
  - `commit()` / `rollback()`
  - The `_insert_stock_movement()` helper (line 80–160) detects `change_amount` vs `difference` columns via PRAGMA
- **No equivalent sale command service exists for the Qt application**

### Existing checkout (CTk only)

- `infrastructure/database_service.py:1007` — `process_checkout_transaction(invoice_id, cart_items, customer_id, vat_rate)`
  - Uses `_get_connection()` → `BEGIN IMMEDIATE TRANSACTION`
  - Re-reads ProductMaster row-per-item
  - Decrements stock, writes audit, creates invoice header + line items
  - Full rollback on any failure
  - **Not currently wired into the Qt application**; using it there would introduce a DatabaseService coupling that the Qt layer intentionally avoids
  - **Requires a VAT rate** (see §3 Financial Facts)

---

## 2. Current Sales Schema

### ProductMaster
```
Barcode       TEXT PRIMARY KEY
Name          TEXT NOT NULL
Stock         INTEGER NOT NULL
ExpiryDate    TEXT NOT NULL
Price         REAL NOT NULL
supplier_id   INTEGER  (optional — migration-added)
```

- CTk-schema code (database_service.py:54-61) also has `CHECK (Stock >= 0)`, `CHECK (Price >= 0)`, and migration columns: `supplier_code TEXT`, `barcode_type TEXT DEFAULT 'EAN13'`, `vat_category INTEGER DEFAULT 6`, `eof_code TEXT`
- **Qt POS validates the 5 required columns** (Barcode, Name, Stock, Price, ExpiryDate) at load time

### invoices
```
id             TEXT PRIMARY KEY
invoice_date   TEXT NOT NULL
subtotal       REAL NOT NULL
vat_amount     REAL NOT NULL
grand_total    REAL NOT NULL
customer_id    INTEGER REFERENCES customers(id)  (optional — migration-added)
```

- **No auto-increment**: invoice `id` is plain TEXT — caller supplies it
- **VAT is mandatory**: both `vat_amount` and `grand_total` are NOT NULL in the schema
- customer_id can be NULL
- Created by `database_service.py:72-78` (CTk) and `database_service.py:166-169` (migration)

### invoice_items
```
id            INTEGER PRIMARY KEY AUTOINCREMENT
invoice_id    TEXT NOT NULL  → REFERENCES invoices(id) ON DELETE CASCADE
barcode       TEXT NOT NULL  → REFERENCES ProductMaster(Barcode) ON DELETE RESTRICT
name          TEXT NOT NULL
quantity      INTEGER NOT NULL CHECK (quantity > 0)
price         REAL NOT NULL CHECK (price >= 0)
```

- **Foreign key to invoices** with `ON DELETE CASCADE`
- **price** is a snapshot of the unit price at sale time, not a live lookup
- No discount, tax-rate, or line-tax columns exist

### stock_movements
```
id              INTEGER PRIMARY KEY AUTOINCREMENT
timestamp       TEXT NOT NULL
barcode         TEXT NOT NULL
product_name    TEXT NOT NULL
old_stock       INTEGER NOT NULL
new_stock       INTEGER NOT NULL
difference      INTEGER NOT NULL        ← legacy schema variant
reference_id    TEXT                    ← legacy schema variant
change_amount   INTEGER                 ← current schema variant
source          TEXT                    ← current schema variant
operator        TEXT DEFAULT "Σύστημα"  ← current schema variant
```

- The repository contains compatibility code for **both** legacy (`difference`, `reference_id`) and current (`change_amount`, `source`, `operator`) schema variants
- This audit did **not** inspect a deployed production database; the active production variant is unknown
- The `_insert_stock_movement()` helper (inventory_command_service.py:80-160) detects columns via PRAGMA and inserts the correct set
- CTk's `_log_stock_movement_on_conn` (database_service.py:1664-1698) tries `change_amount` first, falls back to `difference`
- **Source identifiers seen**: `"Qt Αποθήκη"`, `"POS"`, `"Φόρμα Προϊόντος"`, `"Εισαγωγή"`, `"Τιμολόγιο"`
- Existing test uses `source="POS"` for sales (test_stock_movements.py:21)

### customers
```
id      INTEGER PRIMARY KEY AUTOINCREMENT
name    TEXT NOT NULL
amka    TEXT UNIQUE
phone   TEXT
```

- customer_id on invoices is optional — a sale can be anonymous

### Invoice ID generation strategy

- **Existing pattern**: callers generate IDs before calling `process_checkout_transaction`
- Test examples: `"INV-TEST-1"`, mock data: `"MOCK-INV-1000"` through `"MOCK-INV-1046"`
- **No centralized invoice-number generator, sequence table, or counter exists**
- The Qt layer must either generate its own ID or call into a shared service

---

## 3. Financial Facts

### Product price origin
- `ProductMaster.Price` is the **single authoritative product price**
- No tiered pricing, volume discounts, customer-specific prices, or promotional pricing exists
- The POS catalog displays `Price` directly; the cart stores a **snapshot** of `Price` at the time the product was added to cart

### VAT price semantics — **UNKNOWN**
- The schema has `vat_category INTEGER DEFAULT 6` (a migration-added column on ProductMaster)
- `process_checkout_transaction` takes an explicit `vat_rate: float` parameter
- **Existing tests pass `vat_rate=0.15`**; the repository does **not** establish this as a valid business, pharmacy, or tax policy
- **It is NOT established whether ProductMaster.Price is VAT-inclusive or VAT-exclusive** — this remains blocking and must be decided outside this implementation task
- The invoices table stores `subtotal`, `vat_amount`, and `grand_total` separately
- The existing CTk checkout computes: `vat = round(subtotal * vat_rate, 2)`
- **VAT behavior is explicitly frozen in the current Qt application** — POS Phase A/B0 show no VAT

### Existing subtotal/VAT/grand-total behavior
- `process_checkout_transaction` calculates:
  ```python
  subtotal = round(sum(p.price * q for p, q in succeeded), 2)
  vat = round(subtotal * vat_rate, 2)
  grand = round(subtotal + vat, 2)
  ```
- These three values are stored as-is in the `invoices` row
- No post-calculation adjustment, rounding reconciliation, or tax-breakdown table exists

### Discounts — **NONE**
- No discount columns exist on invoices, invoice_items, or ProductMaster
- No coupon, loyalty-points, or promotional-discount logic exists anywhere in the codebase

### Payment methods — **NONE**
- No `payments`, `payment_methods`, or `transactions` table exists
- No till-balance or cash-register concept exists

### Receipt numbering — **NONE**
- No receipt table or receipt-number generator exists
- Invoice ID doubles as the receipt identifier

---

## 4. Atomic-Sale Contract (Future B2 Specification)

The following transaction must execute inside **one** `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` boundary:

```
1. Authoritative product re-read
   SELECT Barcode, Name, Stock, Price, ExpiryDate
   FROM ProductMaster WHERE Barcode IN (...)

2. Line aggregation
   Sum quantities per barcode; validate price ≥ 0 and isfinite

3. Stock / expiry / price validation (per line)
   - Product must exist
   - Stock ≥ requested quantity
   - ExpiryDate is blank or ≥ today (ISO format)
   - Price is finite, non-negative
   → Any failure: ROLLBACK entire transaction, return per-line errors

4. Invoice header creation
   INSERT INTO invoices (id, invoice_date, subtotal, vat_amount, grand_total, customer_id)
   - id: caller-supplied, unique
   - invoice_date: datetime('now') or caller-supplied
   - subtotal: Σ(price × qty) — **VAT-exclusive or inclusive TBD** (see §5)
   - vat_amount: computed per VAT policy (currently UNKNOWN — see §5)
   - grand_total: subtotal + vat_amount
   - customer_id: optional, NULL for anonymous

5. Invoice item creation (one row per aggregated barcode)
   INSERT INTO invoice_items (invoice_id, barcode, name, quantity, price)
   - price: snapshot of the unit price at sale time

6. Stock decrement + audit (per line)
   UPDATE ProductMaster SET Stock = Stock - qty WHERE Barcode = ?
   INSERT INTO stock_movements (timestamp, barcode, product_name,
       old_stock, new_stock, change_amount, reason, source, operator)
   - reason: "Πώληση"
   - source: "POS" (Qt)
   - operator: TBD (currently "Σύστημα" by default)

7. COMMIT (all-or-nothing)
   → Return immutable POSReceipt with invoice_id, items, totals
```

### Failure guarantees
- **No partial invoice**: invoice header is only INSERTed after all items validate
- **No partial stock change**: stock UPDATE + audit INSERT happen in the same transaction; any failure rolls back
- **No orphan item**: invoice_items uses `FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE` — but since we COMMIT or ROLLBACK as a unit, cascade deletion is a safety net, not a primary mechanism
- **No audit row without sale**: audit INSERT and stock UPDATE are in the same transaction

### Required input validation (reuse from preflight)
- Cart non-empty
- Every line is a valid (barcode: str, qty: int > 0) pair
- Aggregate duplicate barcodes before validation

### Returned receipt object
- `ok: bool`
- `invoice_id: str`
- `items: tuple[ReceiptLine, ...]` (barcode, name, qty, unit_price, line_total)
- `subtotal: float`
- `vat_amount: float`
- `grand_total: float`
- `customer_name: str` (or "—")
- `timestamp: str`

---

## 5. Gaps and Blockers

| # | Blocker | Severity | Detail |
|---|---------|----------|--------|
| 1 | **VAT price semantics** | BLOCKING | Is `ProductMaster.Price` VAT-inclusive or VAT-exclusive? The existing checkout computes VAT _on top_ of the subtotal, implying prices are VAT-exclusive. But no documentation confirms this. Must be explicitly decided **before** any sale is created. |
| 2 | **Invoice ID generation** | BLOCKING | No ID generator exists. Options: UUID, timestamp-based, sequential counter in a `meta` table. Must be chosen and implemented. |
| 3 | **Operator identity** | MEDIUM | `stock_movements.operator` currently defaults to `"Σύστημα"`. A real POS needs operator tracking. Not blocking for Phase B2 if we accept the default. |
| 4 | **Customer selection** | LOW | The current POS has no customer picker. Sales can be anonymous (customer_id=NULL) for Phase B2. |
| 5 | **Payment method** | N/A for B2 | No payment table exists. Deferred until payment integration (Phase C+). |
| 6 | **Receipt numbering** | N/A for B2 | Invoice ID can serve as receipt number for now. |
| 7 | **AADE / ΗΔΙΚΑ integration** | N/A for B2 | Deferred from Phase B2; planned for a later dedicated integration phase. No integration contract is implemented or approved yet. |
| 8 | **Cancellation / returns** | N/A for B2 | No return/credit-note schema or logic exists. Deferred. |
| 9 | **VAT rate configurability** | MEDIUM | `process_checkout_transaction` takes `vat_rate` as parameter. A Qt sale command must do the same — read from config or SystemConfig. The appropriate VAT rate must be decided outside this implementation task. |
| 10 | **Price changes between preflight and checkout** | DESIGN | The preflight uses `_connect_ro()` and the checkout uses a write connection. There is a TOCTOU window. Must re-read stock/price **inside the write transaction** (the atomic-sale contract already accounts for this). |

---

## 6. Proposed Phase B2 Scope

**File**: New `infrastructure/sale_command_service.py`

**Scope** (smallest safe implementation):

```
create_sale(db_path, invoice_id, cart_lines, customer_id=None, vat_rate=0.15)
    → SaleResult (ok, invoice_id, items, subtotal, vat, grand_total)
```

**What it does**:
- Opens one `sqlite3.connect(db_path)` write connection
- `BEGIN IMMEDIATE` + `PRAGMA foreign_keys=ON`
- Re-validates stock/price/expiry against live ProductMaster
- Creates invoice header → invoice_items → stock decrement → audit
- COMMIT or full ROLLBACK
- Returns typed immutable result

**What it does NOT do**:
- Enable checkout button in Qt
- Show VAT in POS UI
- Process payments
- Print receipts
- Handle customers (pass NULL)
- Generate invoice IDs (caller supplies)

**Qt integration (deferred to Phase B3)**:
- Wire the enabled checkout button (currently disabled)
- Call `create_sale` via a QObject worker on QThread
- Show a receipt dialog (read-only, no print)
- Refresh the cart to empty after successful sale

**Tests for Phase B2**:
- Pure-Python (no Qt)
- Covers: valid sale, insufficient stock, expired product, atomic rollback, audit trail correctness, stock decrement exactness, invoice persistence, customer=NULL support

---

## 7. Verification Record

- **No database write commands executed** during this audit
- **No application code modified**
- **File created**: `docs/POS_B1_TRANSACTION_READINESS.md`
- **Commit**: see `git log -1`
