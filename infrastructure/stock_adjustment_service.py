"""Controlled stock adjustment for pilot operations — pharmacist-operated.

One public operation (``adjust_stock``) that sets a product's physically
counted stock inside a BEGIN IMMEDIATE transaction, validates all inputs,
enforces concurrency control, and writes exactly one auditable stock
movement row.  No schema changes, no VAT access, no AI integration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# Typed request / result
# ═══════════════════════════════════════════════════════════════════════

REASON_CHOICES = (
    "Απογραφή",
    "Φθορά / Λήξη",
    "Διόρθωση δεδομένων",
    "Άλλη αιτία",
)

MAX_REASON_LEN = 200
MAX_OPERATOR_LEN = 100
SOURCE_TAG = "Ελεγχόμενη Διόρθωση Αποθέματος"


@dataclass(frozen=True)
class StockAdjustmentRequest:
    """Immutable request for a pharmacist-operated stock adjustment."""
    barcode: str
    expected_current_stock: int
    counted_stock: int
    reason: str
    operator: str = "Φαρμακοποιός"


@dataclass(frozen=True)
class StockAdjustmentResult:
    ok: bool
    message: str = ""
    no_change: bool = False


# ═══════════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════════

def _validate_int(value, label: str) -> Optional[str]:
    """Return a Greek error string if *value* is not a valid int (or is bool)."""
    if isinstance(value, bool):
        return f"Το '{label}' πρέπει να είναι ακέραιος, όχι boolean."
    if not isinstance(value, int):
        return f"Το '{label}' πρέπει να είναι ακέραιος."
    if value < 0:
        return f"Το '{label}' πρέπει να είναι μη αρνητικός ακέραιος."
    return None


def _validate_request(req: StockAdjustmentRequest) -> Optional[str]:
    """Return a Greek error string or None."""
    if not isinstance(req.barcode, str) or not req.barcode.strip():
        return "Το barcode είναι υποχρεωτικό."

    err = _validate_int(req.expected_current_stock, "αναμενόμενο απόθεμα")
    if err:
        return err

    err = _validate_int(req.counted_stock, "καταμετρημένο απόθεμα")
    if err:
        return err

    if not isinstance(req.reason, str) or not req.reason.strip():
        return "Η αιτία διόρθωσης είναι υποχρεωτική."
    if len(req.reason) > MAX_REASON_LEN:
        return f"Η αιτία δεν πρέπει να ξεπερνά τους {MAX_REASON_LEN} χαρακτήρες."

    if not isinstance(req.operator, str):
        return "Το όνομα χειριστή πρέπει να είναι κείμενο."
    if len(req.operator) > MAX_OPERATOR_LEN:
        return f"Το όνομα χειριστή δεν πρέπει να ξεπερνά τους {MAX_OPERATOR_LEN} χαρακτήρες."

    return None


# ═══════════════════════════════════════════════════════════════════════
# Schema detection (reused pattern from inventory_command_service)
# ═══════════════════════════════════════════════════════════════════════

def _has_column(cur, table: str, column: str) -> bool:
    rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    return any(r[1] == column for r in rows)


def _insert_audit_row(
    cur,
    barcode: str,
    name: str,
    old_stock: int,
    new_stock: int,
    difference: int,
    reason: str,
    operator: str,
) -> Optional[str]:
    """Insert one stock_movements row. Returns None on success, Greek error on failure.

    Detects the actual column set via PRAGMA:
    - Required: timestamp, barcode, product_name, old_stock, new_stock, reason
    - Amount: prefer ``change_amount``, else ``difference``
    - Source: prefer ``source``, else ``reference_id`` (legacy)
    - Operator: ``operator`` column when present
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cols = [
        "timestamp", "barcode", "product_name",
        "old_stock", "new_stock", "reason",
    ]
    vals = [now, barcode, name, old_stock, new_stock, reason]

    has_change = _has_column(cur, "stock_movements", "change_amount")
    has_diff = _has_column(cur, "stock_movements", "difference")

    if has_change:
        cols.append("change_amount")
        vals.append(difference)
    elif has_diff:
        cols.append("difference")
        vals.append(difference)
    else:
        return (
            "Αδυναμία καταγραφής κίνησης: "
            "δεν βρέθηκε στήλη change_amount ή difference."
        )

    has_source = _has_column(cur, "stock_movements", "source")
    has_ref = _has_column(cur, "stock_movements", "reference_id")
    if has_source:
        cols.append("source")
        vals.append(SOURCE_TAG)
    elif has_ref:
        cols.append("reference_id")
        vals.append(SOURCE_TAG)

    has_op = _has_column(cur, "stock_movements", "operator")
    if has_op:
        cols.append("operator")
        vals.append(operator)

    plc = ",".join("?" for _ in vals)
    cur.execute(
        f"INSERT INTO stock_movements ({','.join(cols)}) VALUES ({plc})",
        vals,
    )
    return None


# ═══════════════════════════════════════════════════════════════════════
# Public operation
# ═══════════════════════════════════════════════════════════════════════

def adjust_stock(
    db_path: str,
    req: StockAdjustmentRequest,
) -> StockAdjustmentResult:
    """Set a product's physically counted stock with full audit trail.

    One BEGIN IMMEDIATE transaction: read current stock, concurrency check,
    update ProductMaster.Stock, insert one stock_movements audit row.
    If the audit insert fails the product update is rolled back entirely.
    """
    err = _validate_request(req)
    if err:
        return StockAdjustmentResult(ok=False, message=err)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        # 1. Verify product exists + read current stock + name
        cur.execute(
            "SELECT Name, Stock FROM ProductMaster WHERE Barcode = ?",
            (req.barcode,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return StockAdjustmentResult(
                ok=False,
                message=f"Το προϊόν με barcode '{req.barcode}' δεν βρέθηκε.",
            )

        db_name: str = row[0]
        db_stock: int = int(row[1])

        # 2. Concurrency check — reject if current stock differs from expected
        if db_stock != req.expected_current_stock:
            conn.rollback()
            return StockAdjustmentResult(
                ok=False,
                message=(
                    f"Το απόθεμα του προϊόντος '{db_name}' άλλαξε από άλλη ενέργεια "
                    f"(τρέχον: {db_stock}, αναμενόμενο: {req.expected_current_stock}). "
                    f"Κάντε ανανέωση και δοκιμάστε ξανά."
                ),
            )

        # 3. No-change guard — if counted equals current, do nothing
        if req.counted_stock == db_stock:
            conn.rollback()
            return StockAdjustmentResult(
                ok=True,
                message="Το καταμετρημένο απόθεμα είναι ίδιο με το καταγεγραμμένο. "
                        "Δεν έγινε καμία μεταβολή.",
                no_change=True,
            )

        # 4. Update ProductMaster.Stock only
        cur.execute(
            "UPDATE ProductMaster SET Stock = ? WHERE Barcode = ?",
            (req.counted_stock, req.barcode),
        )

        # 5. Insert audit row
        diff = req.counted_stock - db_stock
        audit_err = _insert_audit_row(
            cur,
            barcode=req.barcode,
            name=db_name,
            old_stock=db_stock,
            new_stock=req.counted_stock,
            difference=diff,
            reason=req.reason.strip(),
            operator=req.operator.strip(),
        )
        if audit_err:
            conn.rollback()
            return StockAdjustmentResult(ok=False, message=audit_err)

        # 6. Commit — both update and audit succeed together
        conn.commit()

        sign = "+" if diff >= 0 else ""
        return StockAdjustmentResult(
            ok=True,
            message=(
                f"Το απόθεμα του προϊόντος '{db_name}' διορθώθηκε: "
                f"{db_stock} → {req.counted_stock} ({sign}{diff})."
            ),
            no_change=False,
        )

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        return StockAdjustmentResult(
            ok=False,
            message=f"Σφάλμα βάσης δεδομένων κατά τη διόρθωση αποθέματος: {e}",
        )
    except Exception as e:
        if conn:
            conn.rollback()
        return StockAdjustmentResult(
            ok=False,
            message=f"Σφάλμα κατά τη διόρθωση αποθέματος: {e}",
        )
    finally:
        if conn:
            conn.close()
