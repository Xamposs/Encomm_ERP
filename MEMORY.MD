# 🧠 ENCOMM ERP — ΠΛΗΡΕΣ PROJECT MEMORY & CONTEXT TRACKER
> Τελευταία ενημέρωση: 2026-06-10 12:09 | Agent: OpenClaw Hermes

---

## 1. ΕΤΑΙΡΙΚΟ & ΝΟΜΙΚΟ ΠΛΑΙΣΙΟ

- **Όνομα Εταιρείας:** Encomm Tensor Intelligence Ι.Κ.Ε.
- **Εταιρική Δομή:** Η μετοχική συμμετοχή διαχειρίζεται στο 24% για νομική και επιχειρηματική προστασία.
- **Όνομα Εφαρμογής:** ENCOMM ERP 🧪 (ClawBot AI Integration).
- **Επιχειρηματικός Στόχος:** AI-Native Mini-ERP για φαρμακευτικό τομέα — διαχείριση 100.000+ κωδικών φαρμάκων με zero-latency.
- **Σχεδιασμός Platform:** Η εφαρμογή θα γίνει compile σε `.exe` για Windows 10/11. Κώδικας 100% cross-platform (Windows/Linux).

---

## 2. ARCHITECTURE & TECH STACK

### 2.1 Runtime Environment
- **OS Development:** Linux Mint (X11/Cinnamon desktop).
- **OS Production Target:** Windows 10/11 (.exe via PyInstaller).
- **Python Version:** 3.12
- **Virtual Environment:** `/home/xampos/Downloads/ERP/venv/`

### 2.2 Frontend
- **Framework:** CustomTkinter
- **UI Κανόνας 1 (Χρώματα):** Όλα τα χρώματα στα layouts ορίζονται ΑΠΟΚΛΕΙΣΤΙΚΑ με helper tuples για Light/Dark mode: `fg_color=("light_color", "dark_color")`. Καμία εξαίρεση.
- **UI Κανόνας 2 (Anti-Deadlock):** Στη μέθοδο `select_frame()`, απαγορεύεται η χρήση του σκέτου `self.update()` μέσα σε loop. Προκαλεί X11 event deadlocks και resize lag. Διατηρείται ΜΟΝΟ το `self.update_idletasks()`.
- **UI Κανόνας 3 (Cross-Platform):** Κανένα hardcoded Linux path. Όλα τα paths χρησιμοποιούν `os.path` ή `pathlib`.

### 2.3 Backend & Database
- **Database Engine:** SQLite
- **Βελτιστοποιήσεις:**
  - `PRAGMA journal_mode=WAL`
  - `PRAGMA synchronous=NORMAL`
  - `PRAGMA cache_size=-64000` (64MB cache)
- **Πίνακες:**
  - `ProductMaster` — Barcode (PK), Name, Stock, ExpiryDate, Price
  - `SystemConfig` — Key (PK), Value — persistent key-value store για ρυθμίσεις, άδειες, credentials

### 2.4 Installed Libraries (venv)
- `customtkinter` — UI framework
- `openpyxl` — Excel (.xlsx/.xls) parsing
- `python-dotenv` — environment variables
- `darkdetect` — OS theme detection

---

## 3. PROJECT DIRECTORY STRUCTURE

```
/home/xampos/Downloads/ERP/
├── main.py                              # Entry point — boots the app
├── generate_stress_test_data.py         # 100K mock products CSV generator
├── supplier_invoice_stress_test.csv     # Generated 100K-row test file
├── pharmacy.db                          # SQLite database (auto-created)
├── core/
│   ├── __init__.py
│   ├── domain_models.py                 # Product, Invoice dataclasses
│   └── business_rules.py                # is_low_stock, is_expired, is_near_expiry, calculate_vat, calculate_invoice_totals
├── infrastructure/
│   ├── __init__.py
│   ├── database_service.py              # DatabaseService — all CRUD + bulk_upsert + SystemConfig
│   ├── excel_parser_service.py          # ExcelParserService — CSV/Excel parser
│   └── license_service.py               # HWID generation, license verify/generate
├── presentation/
│   ├── __init__.py
│   └── main_window.py                   # MainWindow (CTk) — full UI: Dashboard, Inventory, POS, Settings, AI
└── venv/                                # Python virtual environment
```

---

## 4. FILE-BY-FILE TECHNICAL DETAILS

### 4.1 `main.py`
- Entry point. Instantiate `DatabaseService` → construct config dict → launch `MainWindow`.

### 4.2 `core/domain_models.py`
- `Product` dataclass: barcode, name, stock, expiry_date, price
- `Invoice` dataclass: invoice structure

### 4.3 `core/business_rules.py`
- `is_low_stock(product, threshold)` — ελέγχει αν stock ≤ threshold
- `is_expired(product, current_date)` — ελέγχει αν η ημερομηνία λήξης έχει παρέλθει
- `is_near_expiry(product, threshold_days, current_date)` — ελέγχει αν λήγει εντός threshold_days
- `get_days_until_expiry(product, current_date)` — υπολογίζει ημέρες μέχρι λήξη
- `calculate_vat(amount, vat_rate)` — υπολογισμός ΦΠΑ
- `calculate_invoice_totals(items, vat_rate)` → (vat_amount, grand_total)
- `parse_date(date_str)` — ασφαλής parser με fallback

### 4.4 `infrastructure/database_service.py`
**Κλάση `DatabaseService`:**
- **Αρχικοποίηση:** Δημιουργεί `ProductMaster` + `SystemConfig` tables αν δεν υπάρχουν. Αν η βάση είναι κενή, κάνει seed με 8 demo φάρμακα.
- **CRUD:**
  - `get_all_products()` → List[Product]
  - `get_product(barcode)` → Optional[Product]
  - `add_product(product)` → bool
  - `update_product(product)` → bool
  - `update_stock(barcode, new_stock)` → bool
  - `delete_product(barcode)` → bool
- **Bulk Import:**
  - `bulk_upsert_products(products_list)` — `executemany` με `INSERT ... ON CONFLICT(Barcode) DO UPDATE SET Stock = Stock + excluded.Stock`. Ένα transaction, `logging.info` start/end.
- **Config Persistence:**
  - `get_config(key, default)` → Optional[str]
  - `set_config(key, value)` — UPSERT στο SystemConfig
  - `get_all_config()` → Dict[str, str]

### 4.5 `infrastructure/excel_parser_service.py`
**Κλάση `ExcelParserService`:**
- `parse_supplier_file(file_path) → list[tuple]`
- **CSV:** `csv.reader` με UTF-8 → fallback ISO-8859-7 (ελληνικά)
- **Excel:** `openpyxl.load_workbook(read_only=True, data_only=True)`
- **Header:** Πρώτη γραμμή skip αυτόματα
- **Casting:** barcode→str, name→str, stock→int, expiry_date→str, price→float
- **Error Handling:** Κάθε malformed γραμμή → `logging.warning` + skip (δεν κρασάρει)
- **Output:** Λίστα tuples `(barcode, name, stock, expiry_date, price)` → απευθείας compatible με `bulk_upsert_products()`

### 4.6 `infrastructure/license_service.py`
- `generate_hwid() → str` — SHA-256 από platform.node + machine + processor + MAC address, cross-platform
- `verify_local_license(license_key, hwid) → (bool, Optional[str])` — επαλήθευση key vs HWID, επιστρέφει (valid, expires_at)
- `generate_license_key(hwid) → str` — admin/dev tool για demo key generation
- **Μηχανισμός:** SHA-256(hwid + "ENCOMM-TENSOR-2026")[:24].upper()
- **Υπόδειξη:** In production θα αντικατασταθεί με external licensing server (AAERP / Keygen.sh)

### 4.7 `presentation/main_window.py`
**Κλάση `MainWindow(CTk)`:**

**Sidebar (5 tabs):**
- 📊 Αρχική (Dashboard)
- 📦 Αποθήκη (Inventory)
- 🧾 Ταμείο / Πωλήσεις (POS)
- ⚙️ Ρυθμίσεις (Settings)
- 🤖 AI Βοηθός (ClawBot — placeholder)

**Dashboard View:**
- 3 stat cards: Συνολικά Προϊόντα / Ελλείψεις Στοκ (amber) / Κοντά στη Λήξη (red)
- Alert table: Zebra-striped rows, critical items (low stock + expired/near-expiry)
- Actions panel: "📥 Εισαγωγή Excel Προμηθευτή" button → `import_supplier_invoice()`

**Inventory View:**
- Search bar (real-time filter by barcode/name)
- ➕ Νέο Προϊόν button → `ProductFormDialog`
- 📥 Εισαγωγή Excel/CSV button (πράσινο tuple: `("#2ecc71", "#27ae60")`) → `import_supplier_invoice()`
- Scrollable table: Barcode, Όνομα, Στοκ, Ημ. Λήξης, Τιμή, Edit/Delete actions
- Zebra striping, color-coded alerts (red=expired, amber=low/near-expiry)

**POS / Invoices View:**
- Product selector (dropdown with stock count)
- Quantity input + 🛒 Προσθήκη στο καλάθι
- Cart table: Όνομα, Τιμή/Μονάδα, Ποσότητα, Σύνολο, ❌ Remove
- Σύνοψη: Τεμάχια, Υποσύνολο, ΦΠΑ%, Γενικό Σύνολο
- 💳 Ολοκλήρωση Πώλησης → stock deduction + receipt (messagebox)
- 🧹 Αδειασμα Καλαθιού

**Settings View (6 sections):**
- ⚙️ Επιχειρηματικοί Κανόνες: ΦΠΑ (0-1), Όριο Στοκ, Όριο Λήξης (ημέρες)
- 🔐 Άδεια & Διασυνδέσεις: HWID (readonly auto-generated), License Key, myDATA User, ΗΔΙΚΑ Code
- 🎨 Εμφάνιση: Dark/Light theme switcher
- 💾 Αποθήκευση → `save_settings_values()`:
  - Γράφει ΦΠΑ/Στοκ/Λήξη σε runtime config + SQLite SystemConfig
  - Γράφει License/myDATA/ΗΔΙΚΑ σε SQLite SystemConfig
  - Αν υπάρχει License Key → το verify μέσω `verify_local_license()`
  - Αν invalid → warning messagebox, license_status = "invalid"

**`import_supplier_invoice()`:**
1. `filedialog.askopenfilename()` (.xlsx/.xls/.csv)
2. `time.time()` start
3. `ExcelParserService().parse_supplier_file(path)`
4. Empty → warning + return
5. `self.db_service.bulk_upsert_products(products)`
6. Duration calculation + `messagebox.showinfo` με count + seconds
7. `refresh_inventory_list()` + `refresh_dashboard()` + `refresh_invoice_view()`
8. Exception handler με `logging.exception` + error messagebox

**`ProductFormDialog(CTkToplevel)`:**
- Modal dialog για Add/Edit product
- Fields: Barcode (locked on edit), Name, Stock, Expiry Date, Price
- Validation: non-empty, stock ≥ 0, price > 0, date format YYYY-MM-DD

### 4.8 `generate_stress_test_data.py`
- Παράγει `supplier_invoice_stress_test.csv` με 100.000 μοναδικά mock φάρμακα
- Columns: Barcode (EAN-13 format `520XXXXXXXXXX`), Name (`Φάρμακο ST-N`), Stock (0-100), ExpiryDate (2026-2029), Price (2.50-90.00)
- Εκτέλεση: `<1 sec` για 100K rows

---

## 5. COMPLETE PROJECT PROGRESS TRACKER

### Phase 1: Foundation (✅ COMPLETED)
- [X] UI/UX Refactor & Anti-Flicker Framework
  - CustomTkinter theme-aware colour helper functions
  - `_is_dark_mode()`, `_zebra_row()`, `_header_bg/fg()`, `_nav_hover/text/active_bg/text()`, `_body_text()`, `_subtle_text()`, `_card_title_text()`, `_stat_border_default()`
  - Anti-flicker frame switching (`select_frame` με `grid_forget` μόνο στο active frame)
  - `self.update_idletasks()` αντί για `self.update()` — X11 deadlock prevention

- [X] ENCOMM Branding & Sidebar Navigation
  - App title: "ENCOMM ERP 🧪"
  - Outfit font family throughout
  - 5-tab sidebar: Dashboard, Inventory, POS, Settings, AI Assistant
  - Version footer: "v1.0.0 Stable | ENCOMM Tensor Intelligence"

### Phase 2: Data Layer (✅ COMPLETED)
- [X] SQLite Bulk Ingestion Engine
  - `bulk_upsert_products()` μέθοδος στο `DatabaseService`
  - `executemany()` με `INSERT ... ON CONFLICT(Barcode) DO UPDATE SET Stock = Stock + excluded.Stock`
  - Ένα transaction για όλο το batch — tested for 100K+ entities
  - PRAGMA optimizations: WAL, NORMAL sync, 64MB cache

- [X] Excel Parser Service (`infrastructure/excel_parser_service.py`)
  - CSV: `csv.reader` με UTF-8 + ISO-8859-7 fallback
  - Excel: `openpyxl` (read_only=True, data_only=True)
  - Strict casting, graceful skip, `logging.warning` per bad row
  - Auto-skip header row

- [X] UI Integration — Import Button in Inventory Toolbar + Dashboard
  - Green tuple button `("#2ecc71", "#27ae60")` στο inventory toolbar
  - Emerald button `#10B981` στο dashboard actions panel
  - `import_supplier_invoice()`: filedialog → parser → bulk_upsert → timer → messagebox → live refresh
  - Full exception handling

### Phase 3: Licensing & Configuration (✅ COMPLETED)
- [X] License Service (`infrastructure/license_service.py`)
  - `generate_hwid()`: SHA-256(platform.node + machine + processor + MAC)
  - `verify_local_license(key, hwid)`: HMAC-style verification, returns (valid, expires_at)
  - `generate_license_key(hwid)`: Dev/admin tool
  - Cross-platform: works on Linux + Windows

- [X] SystemConfig SQLite Table + Persistent Settings
  - `SystemConfig(Key TEXT PK, Value TEXT)` table — auto-created on init
  - `get_config()`, `set_config()`, `get_all_config()` methods
  - Settings View → "💾 Αποθήκευση Ρυθμίσεων" persists ALL values to SQLite:
    - Business: ΦΠΑ, low_stock_threshold, expiry_alert_days
    - Licensing: license_key, license_status, license_expires
    - Integrations: mydata_user, hdika_code
  - Values survive app restart — loaded from DB on `load_settings_values()`

### Phase 4: Testing (✅ COMPLETED)
- [X] Stress Test Generator
  - `generate_stress_test_data.py` — 100.000 μοναδικά mock φάρμακα σε CSV
  - `supplier_invoice_stress_test.csv` — 100.001 lines (header + 100K rows)
  - Mock EAN-13 barcodes, Greek names, random stock/price/expiry

---

## 6. PENDING / NEXT STEPS

- [ ] **Step 5: Native DeepSeek/GLM API Integration for ClawBot AI**
  - Στόχος: Ο AI Βοηθός (sidebar tab) να συνδέεται σε native DeepSeek ή GLM API
  - Chat interface exists (placeholder) στο `main_window.py` — `_init_ai_assistant_frame()`, `send_ai_message()`
  - Χρειάζεται: API client service, streaming response, context injection (product data, stock alerts)

- [ ] **Παραγωγή .exe (PyInstaller)**
  - Target: Windows 10/11 standalone executable
  - Προσοχή: customtkinter assets, openpyxl, database path handling

- [ ] **Production Licensing**
  - Αντικατάσταση mock license με external server (Keygen.sh / AAERP)
  - Trial period logic, feature gating

---

## 7. MEMORY LOCATIONS

| Αρχείο | Διαδρομή | Ρόλος |
|---|---|---|
| **MEMORY.md** (Project) | `/home/xampos/Downloads/ERP/MEMORY.MD` | Project-level context tracker |
| **MEMORY.md** (Agent) | `/home/xampos/.openclaw/workspace/MEMORY.md` | OpenClaw core agent memory (auto-loaded on startup) |
| **system_memory.md** | `/home/xampos/hermes-agent/system_memory.md` | External agent identity memory |

---

## 8. AGENT SELF-MAINTENANCE RULE
- Ο Agent υποχρεούται να ενημερώνει ΜΟΝΟΣ ΤΟΥ αυτό το αρχείο (MEMORY.md) στο τέλος κάθε επιτυχούς υλοποίησης, καταγράφοντας τη νέα κατάσταση του project χωρίς να περιμένει ρητή εντολή από τον χρήστη.

---

## 9. CHRONOLOGICAL SESSION LOG

### Session 1 — 2026-06-10 10:23 → 12:09
1. **10:23** — Δημιουργία `bulk_upsert_products()` στο `infrastructure/database_service.py` (workspace sandbox)
2. **10:28** — Δημιουργία project-level `MEMORY.MD` στο `/home/xampos/Downloads/ERP/`
3. **10:32** — Δημιουργία external agent memory `/home/xampos/hermes-agent/system_memory.md`
4. **10:35** — Δημιουργία core workspace memory `/home/xampos/.openclaw/workspace/MEMORY.md`
5. **10:38** — Δημιουργία `infrastructure/excel_parser_service.py` — CSV/Excel parser με strict casting & graceful skip
6. **10:43** — UI Integration: Import button στο Inventory Toolbar + Dashboard, `import_supplier_invoice()` μέθοδος
7. **10:56** — MEMORY.md update: Steps 2 & 3 marked Completed, added self-maintenance rule
8. **10:56–11:52** — Step 4 Implementation:
   - `infrastructure/license_service.py` — HWID + verify + generate
   - `database_service.py` upgrade — SystemConfig table + config CRUD methods
   - `main_window.py` upgrade — Settings persist to SQLite + License/myDATA/ΗΔΙΚΑ fields
   - `generate_stress_test_data.py` — 100K mock products generator
9. **12:09** — Full MEMORY.md rewrite — comprehensive project documentation with all details
