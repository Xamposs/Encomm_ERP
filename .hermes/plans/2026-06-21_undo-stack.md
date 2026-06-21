# Undo Stack (Στοίβα Αναίρεσης) Implementation Plan

> **For Hermes:** Use delegate_task fleet pattern — split into 3 non-overlapping workstreams.

**Goal:** Add a 5-action undo/redo stack so non-technical Greek pharmacy staff can reverse mistakes with one click.

**Architecture:** A new `core/undo_stack.py` module stores `(description, undo_fn, redo_fn)` tuples. A visible undo/redo button pair lives in the header bar. Every data mutation (add/edit/delete product, sale, customer, supplier) pushes an undo entry before executing. On undo, the action is reversed and a redo entry is pushed.

**Tech Stack:** Python 3.11, CustomTkinter, SQLite (WAL mode)

---

## Architecture: `ActionHistory` Class

```python
# core/undo_stack.py
from typing import Callable, List, Tuple

ActionEntry = Tuple[str, Callable, Callable]  # (description_greek, undo_fn, redo_fn)

class ActionHistory:
    MAX_STACK = 5

    def __init__(self):
        self._undo_stack: List[ActionEntry] = []
        self._redo_stack: List[ActionEntry] = []

    def push(self, description: str, undo_fn: Callable, redo_fn: Callable):
        """Record an action for potential undo. Pops oldest if stack full."""
        if len(self._undo_stack) >= self.MAX_STACK:
            self._undo_stack.pop(0)
        self._undo_stack.append((description, undo_fn, redo_fn))
        self._redo_stack.clear()

    def undo(self) -> str | None:
        """Execute undo. Returns description of undone action, or None if empty."""
        if not self._undo_stack:
            return None
        desc, undo_fn, _ = self._undo_stack.pop()
        undo_fn()  # redo entry will be pushed by the caller if needed
        return desc

    def redo(self) -> str | None:
        """Execute redo. Returns description of redone action, or None if empty."""
        if not self._redo_stack:
            return None
        desc, redo_fn, _ = self._redo_stack.pop()
        redo_fn()
        return desc

    def push_redo(self, description: str, undo_fn: Callable, redo_fn: Callable):
        """After an undo, push the inverse as a redo entry."""
        if len(self._redo_stack) >= self.MAX_STACK:
            self._redo_stack.pop(0)
        self._redo_stack.append((description, undo_fn, redo_fn))

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_description(self) -> str | None:
        return self._undo_stack[-1][0] if self._undo_stack else None

    @property
    def redo_description(self) -> str | None:
        return self._redo_stack[-1][0] if self._redo_stack else None
```

---

## Task 1: Create `core/undo_stack.py`

**Objective:** Create the ActionHistory module.

**Files:**
- Create: `C:\Users\xampos\Desktop\ERP\core\undo_stack.py`

**Step 1:** Write the file with the full `ActionHistory` class shown above.

**Step 2:** Verify syntax:
```bash
cd C:\Users\xampos\Desktop\ERP && python -m py_compile core/undo_stack.py
```
Expected: PASS (no output)

**Step 3:** Commit
```bash
git add core/undo_stack.py
git commit -m "feat: add ActionHistory undo/redo stack module"
```

---

## Task 2: Wire Undo into MainWindow

**Objective:** Add undo/redo buttons to the header bar and the `ActionHistory` instance. Wire undo push calls into all mutation points.

**Files:**
- Modify: `C:\Users\xampos\Desktop\ERP\presentation\main_window.py`

### Step 1: Import and init ActionHistory in `__init__`

Add to imports (around line 12):
```python
from core.undo_stack import ActionHistory
```

Add in `__init__` after `self.cached_hwid = None` (around line 145):
```python
self.action_history = ActionHistory()
```

### Step 2: Add undo/redo buttons to header bar

In `_init_main_panel()`, after the `ai_status_lbl` grid (around line 398), add:

```python
# Undo/Redo buttons (right side, below clock)
self.undo_redo_frame = customtkinter.CTkFrame(self.header_frame, fg_color="transparent")
self.undo_redo_frame.grid(row=1, column=1, sticky="e", pady=(12, 0))

self.undo_btn = customtkinter.CTkButton(
    self.undo_redo_frame, text="↩", width=36, height=36,
    fg_color="transparent", text_color=_nav_text(),
    hover_color=_nav_hover(),
    font=customtkinter.CTkFont(size=16),
    command=self._undo_last_action, state="disabled"
)
self.undo_btn.pack(side="left", padx=(0, 4))

self.redo_btn = customtkinter.CTkButton(
    self.undo_redo_frame, text="↪", width=36, height=36,
    fg_color="transparent", text_color=_nav_text(),
    hover_color=_nav_hover(),
    font=customtkinter.CTkFont(size=16),
    command=self._redo_last_action, state="disabled"
)
self.redo_btn.pack(side="left")
```

### Step 3: Add undo/redo handler methods

Add these methods to the MainWindow class:

```python
def _update_undo_redo_buttons(self):
    """Enable/disable undo/redo buttons based on stack state + update tooltips."""
    if hasattr(self, 'undo_btn'):
        if self.action_history.can_undo:
            self.undo_btn.configure(state="normal")
            # Update tooltip-like hover text via Greek label
        else:
            self.undo_btn.configure(state="disabled")

    if hasattr(self, 'redo_btn'):
        if self.action_history.can_redo:
            self.redo_btn.configure(state="normal")
        else:
            self.redo_btn.configure(state="disabled")

def _undo_last_action(self):
    """Execute the most recent undoable action."""
    desc = self.action_history.undo()
    if desc is None:
        return
    # After undo, push the inverse as a redo entry
    # The undo_fn() already executed — we record the redo capability
    self._update_undo_redo_buttons()
    messagebox.showinfo("Αναίρεση", f"Η ενέργεια αναιρέθηκε:\n{desc}")

def _redo_last_action(self):
    """Re-execute the most recently undone action."""
    desc = self.action_history.redo()
    if desc is None:
        return
    self._update_undo_redo_buttons()
    messagebox.showinfo("Επανάληψη", f"Η ενέργεια επαναλήφθηκε:\n{desc}")
```

### Step 4: Wire undo into `delete_product()`

**Current code (line ~1251):**
```python
def delete_product(self, barcode: str, name: str):
    if messagebox.askyesno("Eπιβεβαίωση Διαγραφής", f"Είστε σίγουροι ότι θέλετε να διαγράψετε το '{name}'?\nΗ ενέργεια αυτή είναι μη αναστρέψιμη."):
        success = self.db_service.delete_product(barcode)
        if success:
            messagebox.showinfo("Διαγραφή", "Το προϊόν διαγράφηκε επιτυχώς.")
            self.refresh_inventory_list()
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία διαγραφής προϊόντος.")
```

**Replace with:**
```python
def delete_product(self, barcode: str, name: str):
    old_product = self.db_service.get_product(barcode)
    if not old_product:
        messagebox.showerror("Σφάλμα", "Το προϊόν δεν βρέθηκε.")
        return

    if messagebox.askyesno("Επιβεβαίωση Διαγραφής",
        f"Είστε σίγουροι ότι θέλετε να διαγράψετε το '{name}';\n"
        f"(Μπορείτε να το επαναφέρετε με Αναίρεση)"):
        # Capture full state for undo
        old_data = {
            "barcode": old_product.barcode,
            "name": old_product.name,
            "stock": old_product.stock,
            "expiry_date": old_product.expiry_date,
            "price": old_product.price,
            "supplier_id": getattr(old_product, 'supplier_id', None),
        }
        success = self.db_service.delete_product(barcode)
        if success:
            # Push undo entry
            def undo_delete():
                self.db_service.restore_product(old_data)
                self.refresh_inventory_list()
                self.refresh_dashboard()

            def redo_delete():
                self.db_service.delete_product(barcode)
                self.refresh_inventory_list()
                self.refresh_dashboard()

            self.action_history.push(
                f"Διαγραφή προϊόντος: {name}",
                undo_delete, redo_delete
            )
            self._update_undo_redo_buttons()
            messagebox.showinfo("Διαγραφή", "Το προϊόν διαγράφηκε επιτυχώς.")
            self.refresh_inventory_list()
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία διαγραφής προϊόντος.")
```

### Step 5: Wire undo into `open_add_product_dialog()`

Find the success path where a new product is added (search for `add_product` call). After a successful `db_service.add_product(new_product)`, push an undo entry:

```python
# After successful add_product(new_product):
def undo_add():
    self.db_service.delete_product(new_product.barcode)
    self.refresh_inventory_list()
    self.refresh_dashboard()

def redo_add():
    self.db_service.add_product(new_product)
    self.refresh_inventory_list()
    self.refresh_dashboard()

self.action_history.push(
    f"Προσθήκη προϊόντος: {new_product.name}",
    undo_add, redo_add
)
self._update_undo_redo_buttons()
```

### Step 6: Wire undo into `open_edit_product_dialog()` success path

After a successful `db_service.update_product(updated_prod)`, capture the old product state first:

```python
# BEFORE calling update_product, fetch old state:
old_product = self.db_service.get_product(barcode)
# ... then after successful update:
def undo_edit():
    from core.domain_models import Product
    restore = Product(
        barcode=old_product.barcode,
        name=old_product.name,
        stock=old_product.stock,
        expiry_date=old_product.expiry_date,
        price=old_product.price,
        supplier_id=getattr(old_product, 'supplier_id', None),
    )
    self.db_service.update_product(restore)
    self.refresh_inventory_list()

def redo_edit():
    self.db_service.update_product(updated_prod)
    self.refresh_inventory_list()

self.action_history.push(
    f"Επεξεργασία προϊόντος: {updated_prod.name}",
    undo_edit, redo_edit
)
self._update_undo_redo_buttons()
```

### Step 7: Wire undo into `process_checkout()` success path

After a successful sale (invoice saved, stock reduced), push an undo entry:

```python
# In the success path of process_checkout(), after clearing cart:
def undo_sale():
    # Reverse stock changes
    for p, qty in succeeded:
        db_p = self.db_service.get_product(p.barcode)
        if db_p:
            new_stock = db_p.stock + qty
            conn2 = self.db_service._get_connection()
            conn2.execute("UPDATE ProductMaster SET Stock = ? WHERE Barcode = ?",
                         (new_stock, p.barcode))
            conn2.commit()
            conn2.close()
    # Delete the invoice
    self.db_service.delete_invoice(invoice_id)
    self.refresh_invoice_view()
    self.refresh_dashboard()

def redo_sale():
    # Re-execute stock reduction + re-create invoice
    for p, qty in succeeded:
        db_p = self.db_service.get_product(p.barcode)
        if db_p and db_p.stock >= qty:
            conn2 = self.db_service._get_connection()
            conn2.execute("UPDATE ProductMaster SET Stock = ? WHERE Barcode = ?",
                         (db_p.stock - qty, p.barcode))
            conn2.commit()
            conn2.close()
    # Re-create invoice with same ID
    items_list = [(p.barcode, p.name, qty, p.price) for p, qty in succeeded]
    self.db_service.save_invoice_transaction(invoice_id, invoice_date, subtotal, vat_amount, grand_total, items_list)
    self.refresh_invoice_view()
    self.refresh_dashboard()

self.action_history.push(
    f"Πώληση {invoice_id} — {len(succeeded)} είδη, €{grand_total:.2f}",
    undo_sale, redo_sale
)
self._update_undo_redo_buttons()
```

### Step 8: Wire undo into customer delete

Same pattern as product delete — capture `get_customer_by_id(customer_id)` before deleting, push restore entry.

### Step 9: Wire undo into supplier delete

Same pattern — capture `get_supplier_by_id(supplier_id)` before deleting, push restore entry.

### Step 10: Verify syntax
```bash
cd C:\Users\xampos\Desktop\ERP && python -m py_compile presentation/main_window.py
```
Must PASS.

### Step 11: Commit
```bash
git add presentation/main_window.py
git commit -m "feat: wire 5-level undo/redo into all mutation points"
```

---

## Task 3: Add DB Helper Methods

**Objective:** Add `restore_product()`, `get_customer_by_id()`, `get_supplier_by_id()`, `delete_invoice()`, and `restore_customer()` methods needed by undo operations.

**Files:**
- Modify: `C:\Users\xampos\Desktop\ERP\infrastructure\database_service.py`

### Methods to add:

```python
def restore_product(self, data: dict) -> bool:
    """Restore a previously deleted product from captured state."""
    conn = None
    try:
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (data["barcode"], data["name"], data["stock"],
              data["expiry_date"], data["price"], data.get("supplier_id")))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logging.error("Error restoring product: %s", e)
        return False
    finally:
        if conn:
            conn.close()

def get_customer_by_id(self, customer_id: int) -> dict | None:
    """Get full customer record by ID for undo capture."""
    conn = None
    try:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

def get_supplier_by_id(self, supplier_id: int) -> dict | None:
    """Get full supplier record by ID for undo capture."""
    conn = None
    try:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

def delete_invoice(self, invoice_id: str) -> bool:
    """Delete an invoice and its items (CASCADE handles items)."""
    conn = None
    try:
        conn = self._get_connection()
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logging.error("Error deleting invoice: %s", e)
        return False
    finally:
        if conn:
            conn.close()

def restore_customer(self, data: dict) -> bool:
    """Restore a previously deleted customer."""
    conn = None
    try:
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO customers (id, name, amka, phone)
            VALUES (?, ?, ?, ?)
        """, (data["id"], data["name"], data.get("amka", ""), data.get("phone", "")))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logging.error("Error restoring customer: %s", e)
        return False
    finally:
        if conn:
            conn.close()

def restore_supplier(self, data: dict) -> bool:
    """Restore a previously deleted supplier."""
    conn = None
    try:
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO suppliers (id, name, phone, email, address)
            VALUES (?, ?, ?, ?, ?)
        """, (data["id"], data["name"], data.get("phone", ""),
              data.get("email", ""), data.get("address", "")))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logging.error("Error restoring supplier: %s", e)
        return False
    finally:
        if conn:
            conn.close()
```

### Verify:
```bash
cd C:\Users\xampos\Desktop\ERP && python -m py_compile infrastructure/database_service.py
```
Must PASS.

### Commit:
```bash
git add infrastructure/database_service.py
git commit -m "feat: add undo-support DB methods (restore_product, delete_invoice, restore_customer, restore_supplier)"
```

---

## Task 4: End-to-End Verification (Python Pipeline)

After all three tasks complete, run the full verification pipeline:

### Step 1: Full syntax audit
```bash
for f in main.py core/domain_models.py core/business_rules.py core/undo_stack.py infrastructure/database_service.py presentation/main_window.py; do
    python -m py_compile "$f" && echo "PASS $f" || echo "FAIL $f"
done
```
All 6 must PASS.

### Step 2: Import resolution
```bash
cd C:\Users\xampos\Desktop\ERP && python -c "
import sys; sys.path.insert(0, '.')
from core.undo_stack import ActionHistory
ah = ActionHistory()
ah.push('test', lambda: None, lambda: None)
assert ah.can_undo == True
ah.undo()
assert ah.can_undo == False
print('PASS: ActionHistory smoke test')
"
```
Must output: `PASS: ActionHistory smoke test`

### Step 3: Verify undo button widget references
```bash
grep -c "undo_btn\|redo_btn\|action_history\|_undo_last_action\|_redo_last_action" presentation/main_window.py
```
Must return >= 8 matches.

### Step 4: DB method verification
```bash
cd C:\Users\xampos\Desktop\ERP && python -c "
import sys; sys.path.insert(0, '.')
from infrastructure.database_service import DatabaseService
d = DatabaseService()
# Verify new methods exist
for m in ['restore_product', 'get_customer_by_id', 'get_supplier_by_id', 'delete_invoice', 'restore_customer', 'restore_supplier']:
    assert hasattr(d, m), f'Missing: {m}'
    print(f'  ✓ {m}')
print('PASS: All undo DB methods present')
"
```
All 6 methods must be present.

### Step 5: Commit final state
```bash
git push origin main
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Undo of sale reverses stock but invoice_items may have been linked to customer | Only undo sales that have a valid invoice_id + no linked prescription |
| Undo functions capture variables by reference → stale data | Use closure with explicit local copies (e.g., `old_data = dict(old_product.__dict__)`) |
| Redo after undo of delete may fail if barcode reused | Product barcodes are unique in DB; restore uses original barcode |
| Undo button not visible on narrow screens | Place in header row next to clock; hide clock on narrow to make room |

## Edge Cases

- **Undo stack full (5 entries):** Oldest entry silently dropped (FIFO eviction)
- **Redo stack clears on new action:** By design — branching undo history not supported
- **Undo of empty cart sale:** Guarded by `if not self.invoice_cart` check before sale
- **Concurrent undo + refresh:** All undo functions dispatch UI refreshes via synchronously called methods
- **Undo across tab switches:** Works — refresh methods guard against unmapped frames
