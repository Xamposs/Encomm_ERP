"""Safe product write commands for the Qt presentation layer.

Every write goes through this module.  Each command uses one explicit
SQLite transaction with BEGIN IMMEDIATE and rolls back entirely on any
failure.  The audit schema is detected via PRAGMA — no hardcoded columns.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Tuple


# ═══════════════════════════════════════════════════════════════════════
# Typed models
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CreateProductRequest:
    barcode: str
    name: str
    stock: int
    expiry_date: str
    price: float
    supplier_id: int | None = None


@dataclass(frozen=True)
class ProductSnapshot:
    """Immutable fingerprint of editable fields for concurrency control."""
    barcode: str
    name: str
    stock: int
    expiry_date: str
    price: float
    supplier_id: int | None


@dataclass(frozen=True)
class UpdateProductRequest:
    barcode: str
    name: str
    stock: int
    expiry_date: str
    price: float
    supplier_id: int | None = None
    original: ProductSnapshot | None = None


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str = ""
    past_expiry: bool = False


# ═══════════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════════

def _validate_price(val) -> tuple[float | None, str | None]:
    """Return (rounded_value, error_message)."""
    if val is None or isinstance(val, bool):
        return None, "Η τιμή πρέπει να είναι αριθμός."
    if isinstance(val, str):
        return None, "Η τιμή πρέπει να είναι αριθμός."
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None, "Η τιμή πρέπει να είναι αριθμός."
    if math.isnan(f) or math.isinf(f):
        return None, "Η τιμή πρέπει να είναι αριθμός."
    if f < 0:
        return None, "Η τιμή πρέπει να είναι μη αρνητικός αριθμός."
    return round(f, 2), None


def _validate_stock(val) -> tuple[int | None, str | None]:
    """Return (stock, error_message)."""
    if isinstance(val, bool):
        return None, "Το απόθεμα πρέπει να είναι ακέραιος."
    if not isinstance(val, int):
        return None, "Το απόθεμα πρέπει να είναι ακέραιος."
    if val < 0:
        return None, "Το απόθεμα πρέπει να είναι μη αρνητικός ακέραιος."
    return val, None


def _validate_common(
    barcode: str, name: str, expiry_date: str,
) -> str | None:
    if not isinstance(barcode, str) or not barcode.strip():
        return "Το barcode είναι υποχρεωτικό."
    if not isinstance(name, str) or not name.strip():
        return "Το όνομα προϊόντος είναι υποχρεωτικό."
    try:
        date.fromisoformat(expiry_date)
    except (ValueError, TypeError):
        return f"Μη έγκυρη ημερομηνία λήξης: {expiry_date}. YYYY-MM-DD."
    return None


def _past_expiry_flag(expiry_date: str) -> bool:
    try:
        return date.fromisoformat(expiry_date) < date.today()
    except (ValueError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════════
# Schema detection
# ═══════════════════════════════════════════════════════════════════════

def _has_column(cur, table: str, column: str) -> bool:
    rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    return any(r[1] == column for r in rows)


# ═══════════════════════════════════════════════════════════════════════
# Shared audit helper (one implementation for both create and update)
# ═══════════════════════════════════════════════════════════════════════

def _insert_stock_movement(
    cur, barcode: str, name: str,
    old_stock: int, new_stock: int, change: int,
    reason: str,
) -> str | None:
    """Insert one audit row.  Returns None on success, Greek error on failure.

    Detects the actual column set via PRAGMA:
    - Required: timestamp, barcode, product_name, old_stock, new_stock, reason
    - Amount: prefer ``change_amount``, else ``difference``
    - Source: prefer ``source``, else ``reference_id`` (legacy)
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cols = ["timestamp", "barcode", "product_name",
            "old_stock", "new_stock", "reason"]
    vals = [now, barcode, name, old_stock, new_stock, reason]

    has_change = _has_column(cur, "stock_movements", "change_amount")
    has_diff = _has_column(cur, "stock_movements", "difference")

    if has_change:
        cols.append("change_amount")
        vals.append(change)
    elif has_diff:
        cols.append("difference")
        vals.append(change)
    else:
        return ("Αδυναμία καταγραφής κίνησης: "
                "δεν βρέθηκε στήλη change_amount ή difference.")

    has_source = _has_column(cur, "stock_movements", "source")
    has_ref = _has_column(cur, "stock_movements", "reference_id")
    if has_source:
        cols.append("source")
        vals.append("Qt Αποθήκη")
    elif has_ref:
        cols.append("reference_id")
        vals.append("Qt Αποθήκη")

    plc = ",".join("?" for _ in vals)
    cur.execute(
        f"INSERT INTO stock_movements ({','.join(cols)}) VALUES ({plc})",
        vals)
    return None  # success


# ═══════════════════════════════════════════════════════════════════════
# create_product
# ═══════════════════════════════════════════════════════════════════════

def create_product(db_path: str, req: CreateProductRequest) -> CommandResult:
    barcode = req.barcode.strip() if isinstance(req.barcode, str) else ""
    name = req.name.strip() if isinstance(req.name, str) else ""

    stock, s_err = _validate_stock(req.stock)
    if s_err:
        return CommandResult(ok=False, message=s_err)

    price, p_err = _validate_price(req.price)
    if p_err:
        return CommandResult(ok=False, message=p_err)

    err = _validate_common(barcode, name, req.expiry_date)
    if err:
        return CommandResult(ok=False, message=err)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM ProductMaster WHERE Barcode=?", (barcode,))
        if cur.fetchone():
            conn.rollback()
            return CommandResult(ok=False,
                message=f"Το barcode '{barcode}' υπάρχει ήδη.")

        if req.supplier_id is not None:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'")
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(ok=False,
                    message="Ο πίνακας προμηθευτών δεν υπάρχει στη βάση δεδομένων.")
            cur.execute("SELECT 1 FROM suppliers WHERE id=?", (req.supplier_id,))
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(ok=False,
                    message=f"Ο προμηθευτής με ID {req.supplier_id} δεν βρέθηκε.")

        has_sid = _has_column(cur, "ProductMaster", "supplier_id")
        if req.supplier_id is not None and not has_sid:
            conn.rollback()
            return CommandResult(ok=False,
                message="Η στήλη supplier_id δεν υπάρχει στη βάση δεδομένων.")

        cols = ["Barcode", "Name", "Stock", "ExpiryDate", "Price"]
        vals = [barcode, name, stock, req.expiry_date, price]
        if has_sid:
            cols.append("supplier_id")
            vals.append(req.supplier_id)
        cur.execute(
            f"INSERT INTO ProductMaster ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in vals)})", vals)

        audit_err = _insert_stock_movement(
            cur, barcode, name, 0, stock, stock, "Εισαγωγή")
        if audit_err:
            conn.rollback()
            return CommandResult(ok=False, message=audit_err)

        conn.commit()
        return CommandResult(ok=True,
            message="Το προϊόν δημιουργήθηκε.",
            past_expiry=_past_expiry_flag(req.expiry_date))

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        return CommandResult(ok=False, message=f"Σφάλμα: {e}")
    except Exception as e:
        if conn:
            conn.rollback()
        return CommandResult(ok=False, message=f"Σφάλμα: {e}")
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# update_product
# ═══════════════════════════════════════════════════════════════════════

def update_product(db_path: str, req: UpdateProductRequest) -> CommandResult:
    barcode = req.barcode.strip() if isinstance(req.barcode, str) else ""

    # Require original snapshot
    if req.original is None:
        return CommandResult(ok=False,
            message="Λείπει το αρχικό στιγμιότυπο για έλεγχο ταυτοχρονισμού.")
    if req.original.barcode != barcode:
        return CommandResult(ok=False,
            message="Το barcode του αρχικού στιγμιότυπου δεν ταιριάζει.")

    name = req.name.strip() if isinstance(req.name, str) else ""

    stock, s_err = _validate_stock(req.stock)
    if s_err:
        return CommandResult(ok=False, message=s_err)

    price, p_err = _validate_price(req.price)
    if p_err:
        return CommandResult(ok=False, message=p_err)

    err = _validate_common(barcode, name, req.expiry_date)
    if err:
        return CommandResult(ok=False, message=err)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        has_sid = _has_column(cur, "ProductMaster", "supplier_id")
        sel = ("SELECT Barcode, Name, Stock, ExpiryDate, Price"
               + (", supplier_id" if has_sid else ", NULL AS supplier_id")
               + " FROM ProductMaster WHERE Barcode=?")
        cur.execute(sel, (barcode,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return CommandResult(ok=False,
                message=f"Το προϊόν '{barcode}' δεν βρέθηκε.")

        current = ProductSnapshot(
            barcode=row[0], name=row[1], stock=row[2],
            expiry_date=row[3], price=row[4], supplier_id=row[5],
        )

        # Concurrency check
        if (req.original.name != current.name
                or req.original.stock != current.stock
                or req.original.expiry_date != current.expiry_date
                or req.original.price != current.price
                or req.original.supplier_id != current.supplier_id):
            conn.rollback()
            return CommandResult(ok=False,
                message="Το προϊόν άλλαξε από άλλη ενέργεια. "
                        "Κάντε ανανέωση και δοκιμάστε ξανά.")

        # Supplier validation
        if req.supplier_id is not None:
            if not has_sid:
                conn.rollback()
                return CommandResult(ok=False,
                    message="Η στήλη supplier_id δεν υπάρχει στη βάση δεδομένων.")
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'")
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(ok=False,
                    message="Ο πίνακας προμηθευτών δεν υπάρχει στη βάση δεδομένων.")
            cur.execute("SELECT 1 FROM suppliers WHERE id=?", (req.supplier_id,))
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(ok=False,
                    message=f"Ο προμηθευτής με ID {req.supplier_id} δεν βρέθηκε.")

        # Build UPDATE SET
        set_clauses = ["Name=?", "Stock=?", "ExpiryDate=?", "Price=?"]
        set_vals = [name, stock, req.expiry_date, price]
        if has_sid:
            set_clauses.append("supplier_id=?")
            set_vals.append(req.supplier_id)
        set_vals.append(barcode)
        cur.execute(
            f"UPDATE ProductMaster SET {','.join(set_clauses)} WHERE Barcode=?",
            set_vals)

        # Audit only if stock changed
        if stock != current.stock:
            change = stock - current.stock
            audit_err = _insert_stock_movement(
                cur, barcode, name, current.stock, stock, change,
                "Χειροκίνητη Ενημέρωση")
            if audit_err:
                conn.rollback()
                return CommandResult(ok=False, message=audit_err)

        conn.commit()
        return CommandResult(ok=True,
            message="Το προϊόν ενημερώθηκε.",
            past_expiry=_past_expiry_flag(req.expiry_date))

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        return CommandResult(ok=False, message=f"Σφάλμα: {e}")
    except Exception as e:
        if conn:
            conn.rollback()
        return CommandResult(ok=False, message=f"Σφάλμα: {e}")
    finally:
        if conn:
            conn.close()
