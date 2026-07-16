"""Safe product write commands for the Qt presentation layer.

Every write goes through this module — never directly from Qt widgets.
Each command uses one explicit SQLite transaction with BEGIN IMMEDIATE
and rolls back entirely on any failure.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
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
    original: ProductSnapshot | None = None  # for concurrency check


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str = ""          # Greek success or error description
    past_expiry: bool = False  # warning flag: user confirmed past date


# ═══════════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════════

def _validate_common(
    barcode: str, name: str, stock: int, expiry_date: str, price: float,
) -> str | None:
    """Return Greek error string or None."""
    if not barcode or not barcode.strip():
        return "Το barcode είναι υποχρεωτικό."
    if not name or not name.strip():
        return "Το όνομα προϊόντος είναι υποχρεωτικό."
    if not isinstance(stock, int) or stock < 0:
        return "Το απόθεμα πρέπει να είναι μη αρνητικός ακέραιος."
    if not isinstance(price, (int, float)) or price < 0:
        return "Η τιμή πρέπει να είναι μη αρνητικός αριθμός."
    try:
        date.fromisoformat(expiry_date)
    except (ValueError, TypeError):
        return f"Μη έγκυρη ημερομηνία λήξης: {expiry_date}. Χρησιμοποιήστε YYYY-MM-DD."
    return None


def _past_expiry_flag(expiry_date: str) -> bool:
    """Return True if the expiry date is strictly before today."""
    try:
        return date.fromisoformat(expiry_date) < date.today()
    except (ValueError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════════
# Schema detection (read-only)
# ═══════════════════════════════════════════════════════════════════════

def _has_column(cur, table: str, column: str) -> bool:
    rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    return any(r[1] == column for r in rows)


# ═══════════════════════════════════════════════════════════════════════
# create_product
# ═══════════════════════════════════════════════════════════════════════

def create_product(
    db_path: str, req: CreateProductRequest,
) -> CommandResult:
    """Insert a new product with audit trail.

    One transaction: product INSERT + stock_movement INSERT.
    Rolls back entirely on any error.
    """
    barcode = req.barcode.strip()
    name = req.name.strip()
    stock = req.stock
    expiry_date = req.expiry_date
    price = round(req.price, 2)

    err = _validate_common(barcode, name, stock, expiry_date, price)
    if err:
        return CommandResult(ok=False, message=err)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        # Check duplicate
        cur.execute("SELECT 1 FROM ProductMaster WHERE Barcode=?", (barcode,))
        if cur.fetchone():
            conn.rollback()
            return CommandResult(
                ok=False,
                message=f"Το barcode '{barcode}' υπάρχει ήδη.")

        # Check supplier
        if req.supplier_id is not None:
            cur.execute("SELECT 1 FROM suppliers WHERE id=?", (req.supplier_id,))
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(
                    ok=False,
                    message=f"Ο προμηθευτής με ID {req.supplier_id} δεν βρέθηκε.")

        # Detect available columns
        has_supplier_id = _has_column(cur, "ProductMaster", "supplier_id")

        # Build INSERT
        columns = ["Barcode", "Name", "Stock", "ExpiryDate", "Price"]
        values = [barcode, name, stock, expiry_date, price]
        if has_supplier_id:
            columns.append("supplier_id")
            values.append(req.supplier_id)
        plc = ",".join("?" for _ in values)
        cols = ",".join(columns)
        cur.execute(
            f"INSERT INTO ProductMaster ({cols}) VALUES ({plc})", values)

        # Audit: stock_movements
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        audit_cols = ["timestamp", "barcode", "product_name",
                       "old_stock", "new_stock", "difference",
                       "reason"]
        audit_vals = [now, barcode, name, 0, stock, stock,
                       "Εισαγωγή"]
        if _has_column(cur, "stock_movements", "source"):
            audit_cols.append("source")
            audit_vals.append("Qt Αποθήκη")
        if _has_column(cur, "stock_movements", "change_amount"):
            audit_cols.append("change_amount")
            audit_vals.append(stock)
        a_plc = ",".join("?" for _ in audit_vals)
        a_cols = ",".join(audit_cols)
        cur.execute(
            f"INSERT INTO stock_movements ({a_cols}) VALUES ({a_plc})",
            audit_vals)

        conn.commit()
        past = _past_expiry_flag(expiry_date)
        return CommandResult(ok=True, message="Το προϊόν δημιουργήθηκε.", past_expiry=past)

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        return CommandResult(
            ok=False,
            message=f"Σφάλμα δημιουργίας προϊόντος: {e}")
    except Exception as e:
        if conn:
            conn.rollback()
        return CommandResult(
            ok=False,
            message=f"Σφάλμα δημιουργίας προϊόντος: {e}")
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# update_product
# ═══════════════════════════════════════════════════════════════════════

def update_product(
    db_path: str, req: UpdateProductRequest,
) -> CommandResult:
    """Update editable fields of an existing product with concurrency check.

    Loads current state, compares against original snapshot.  If any
    editable field changed since the snapshot was taken, the update is
    rejected with a Greek conflict message.
    """
    barcode = req.barcode.strip()
    name = req.name.strip()
    stock = req.stock
    expiry_date = req.expiry_date
    price = round(req.price, 2)

    err = _validate_common(barcode, name, stock, expiry_date, price)
    if err:
        return CommandResult(ok=False, message=err)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        # Load current
        cur.execute(
            "SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id "
            "FROM ProductMaster WHERE Barcode=?", (barcode,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return CommandResult(
                ok=False,
                message=f"Το προϊόν '{barcode}' δεν βρέθηκε.")

        current = ProductSnapshot(
            barcode=row[0],
            name=row[1],
            stock=row[2],
            expiry_date=row[3],
            price=row[4],
            supplier_id=row[5],
        )

        # Concurrency check
        if req.original is not None and req.original != current:
            # Only compare editable fields
            if (req.original.name != current.name
                    or req.original.stock != current.stock
                    or req.original.expiry_date != current.expiry_date
                    or req.original.price != current.price
                    or req.original.supplier_id != current.supplier_id):
                conn.rollback()
                return CommandResult(
                    ok=False,
                    message="Το προϊόν άλλαξε από άλλη ενέργεια. "
                            "Κάντε ανανέωση και δοκιμάστε ξανά.")

        # Check supplier
        if req.supplier_id is not None:
            cur.execute("SELECT 1 FROM suppliers WHERE id=?", (req.supplier_id,))
            if not cur.fetchone():
                conn.rollback()
                return CommandResult(
                    ok=False,
                    message=f"Ο προμηθευτής με ID {req.supplier_id} δεν βρέθηκε.")

        has_supplier_id = _has_column(cur, "ProductMaster", "supplier_id")

        # Build UPDATE SET
        set_clauses = ["Name=?", "Stock=?", "ExpiryDate=?", "Price=?"]
        set_vals = [name, stock, expiry_date, price]
        if has_supplier_id:
            set_clauses.append("supplier_id=?")
            set_vals.append(req.supplier_id)
        set_vals.append(barcode)
        cur.execute(
            f"UPDATE ProductMaster SET {','.join(set_clauses)} WHERE Barcode=?",
            set_vals)

        # Audit: only if stock changed
        if stock != current.stock:
            now = datetime.now().isoformat(sep=" ", timespec="seconds")
            diff = stock - current.stock
            audit_cols = ["timestamp", "barcode", "product_name",
                           "old_stock", "new_stock", "difference",
                           "reason"]
            audit_vals = [now, barcode, name,
                           current.stock, stock, diff,
                           "Χειροκίνητη Ενημέρωση"]
            if _has_column(cur, "stock_movements", "source"):
                audit_cols.append("source")
                audit_vals.append("Qt Αποθήκη")
            if _has_column(cur, "stock_movements", "change_amount"):
                audit_cols.append("change_amount")
                audit_vals.append(diff)
            a_plc = ",".join("?" for _ in audit_vals)
            a_cols = ",".join(audit_cols)
            cur.execute(
                f"INSERT INTO stock_movements ({a_cols}) VALUES ({a_plc})",
                audit_vals)

        conn.commit()
        past = _past_expiry_flag(expiry_date)
        return CommandResult(ok=True, message="Το προϊόν ενημερώθηκε.", past_expiry=past)

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        return CommandResult(
            ok=False,
            message=f"Σφάλμα ενημέρωσης προϊόντος: {e}")
    except Exception as e:
        if conn:
            conn.rollback()
        return CommandResult(
            ok=False,
            message=f"Σφάλμα ενημέρωσης προϊόντος: {e}")
    finally:
        if conn:
            conn.close()
