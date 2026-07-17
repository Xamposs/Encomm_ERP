"""
Supplier goods receipts — draft, review, approve, cancel.

Phase D1: Approval-controlled, lot-ready stock intake.
Uses dedicated goods_receipt* tables — does NOT touch sales invoices.
"""

from __future__ import annotations

import os
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from infrastructure.database_service import DatabaseService


# ═══════════════════════════════════════════════════════════════════════
# Typed models
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GoodsReceiptLine:
    line_number: int
    barcode: str
    product_name: str
    received_qty: int
    unit_cost: float
    batch_number: str = ""
    expiry_date: str = ""


@dataclass(frozen=True)
class GoodsReceipt:
    id: str
    supplier_id: int
    supplier_name: str
    document_number: str
    document_type: str
    status: str
    received_at: str
    approved_at: str | None
    approved_by: str | None
    notes: str
    created_at: str
    lines: tuple[GoodsReceiptLine, ...]


@dataclass(frozen=True)
class ReceiptListResult:
    ok: bool
    error_message: str = ""
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: tuple[tuple, ...] = ()  # (id, supplier_name, doc_number, doc_type, received_at, status)


@dataclass(frozen=True)
class CreateDraftResult:
    ok: bool
    error_message: str = ""
    receipt_id: str = ""


@dataclass(frozen=True)
class GetReceiptResult:
    ok: bool
    error_message: str = ""
    receipt: GoodsReceipt | None = None


@dataclass(frozen=True)
class ApproveReceiptResult:
    ok: bool
    error_message: str = ""
    receipt_id: str = ""
    lines_applied: int = 0
    total_units: int = 0


@dataclass(frozen=True)
class CancelReceiptResult:
    ok: bool
    error_message: str = ""
    receipt_id: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Connection helper
# ═══════════════════════════════════════════════════════════════════════

def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a read-write connection with foreign keys enabled."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Η βάση δεδομένων δεν βρέθηκε: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ═══════════════════════════════════════════════════════════════════════
# Schema initialization (idempotent)
# ═══════════════════════════════════════════════════════════════════════

def ensure_goods_receipt_schema(conn: sqlite3.Connection) -> None:
    """Create goods_receipt* and stock_lots tables if they don't exist.

    Called from every public operation — idempotent and safe on every
    database, including fresh installs.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goods_receipts (
            id              TEXT PRIMARY KEY,
            supplier_id     INTEGER NOT NULL,
            document_number TEXT    NOT NULL,
            document_type   TEXT    NOT NULL,
            status          TEXT    NOT NULL,
            received_at     TEXT    NOT NULL,
            approved_at     TEXT,
            approved_by     TEXT,
            notes           TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
            UNIQUE (supplier_id, document_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goods_receipt_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id   TEXT    NOT NULL,
            line_number  INTEGER NOT NULL,
            barcode      TEXT    NOT NULL,
            product_name TEXT    NOT NULL,
            received_qty INTEGER NOT NULL CHECK(received_qty >= 0),
            unit_cost    REAL    NOT NULL CHECK(unit_cost >= 0),
            batch_number TEXT    NOT NULL DEFAULT '',
            expiry_date  TEXT    NOT NULL DEFAULT '',
            FOREIGN KEY (receipt_id) REFERENCES goods_receipts(id) ON DELETE CASCADE,
            UNIQUE (receipt_id, line_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_lots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode     TEXT    NOT NULL,
            batch_number TEXT   NOT NULL DEFAULT '',
            expiry_date TEXT    NOT NULL DEFAULT '',
            quantity    INTEGER NOT NULL CHECK(quantity >= 0),
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            FOREIGN KEY (barcode) REFERENCES ProductMaster(Barcode),
            UNIQUE (barcode, batch_number, expiry_date)
        )
    """)


# ═══════════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════════

def _normalize_search(value: str) -> str:
    """Greek accent- and case-insensitive normalization (NFD + strip Mn)."""
    s = str(value).casefold()
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    return unicodedata.normalize("NFC", stripped)


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards so they are treated as literal text."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_receipt_line(line: dict, idx: int) -> str | None:
    """Return a Greek error message or None."""
    if not isinstance(line.get("barcode"), str) or not line["barcode"].strip():
        return f"Γραμμή {idx + 1}: Το barcode είναι υποχρεωτικό."
    if not isinstance(line.get("product_name"), str) or not line["product_name"].strip():
        return f"Γραμμή {idx + 1}: Το όνομα προϊόντος είναι υποχρεωτικό."
    qty = line.get("received_qty")
    if isinstance(qty, bool) or not isinstance(qty, int):
        return f"Γραμμή {idx + 1}: Η ποσότητα πρέπει να είναι ακέραιος."
    if qty < 0:
        return f"Γραμμή {idx + 1}: Η ποσότητα πρέπει να είναι >= 0."
    cost = line.get("unit_cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return f"Γραμμή {idx + 1}: Το κόστος μονάδας πρέπει να είναι αριθμός."
    if cost < 0:
        return f"Γραμμή {idx + 1}: Το κόστος μονάδας πρέπει να είναι >= 0."
    batch = line.get("batch_number", "")
    if not isinstance(batch, str):
        return f"Γραμμή {idx + 1}: Το part number πρέπει να είναι κείμενο."
    expiry = line.get("expiry_date", "")
    if not isinstance(expiry, str):
        return f"Γραμμή {idx + 1}: Η ημερομηνία λήξης πρέπει να είναι κείμενο."
    if expiry:
        try:
            date.fromisoformat(expiry)
        except (ValueError, TypeError):
            return f"Γραμμή {idx + 1}: Μη έγκυρη ημερομηνία λήξης '{expiry}'. YYYY-MM-DD."
    return None


# ═══════════════════════════════════════════════════════════════════════
# Public operations
# ═══════════════════════════════════════════════════════════════════════

def create_receipt_draft(
    db_path: str,
    supplier_id: int,
    document_number: str,
    document_type: str,
    lines: list[dict],
    notes: str = "",
    received_at: str = "",
) -> CreateDraftResult:
    """Create a new goods receipt in draft status.

    Writes only to goods_receipts and goods_receipt_items.
    No ProductMaster stock change and no stock_movements audit rows.
    """
    # ── Input validation ──────────────────────────────────────────
    if not isinstance(supplier_id, int):
        return CreateDraftResult(False, "Το ID προμηθευτή πρέπει να είναι ακέραιος.")
    if not isinstance(document_number, str) or not document_number.strip():
        return CreateDraftResult(False, "Ο αριθμός παραστατικού είναι υποχρεωτικός.")
    if document_type not in ("delivery_note", "supplier_invoice"):
        return CreateDraftResult(False,
            "Ο τύπος παραστατικού πρέπει να είναι 'delivery_note' ή 'supplier_invoice'.")
    if not isinstance(lines, (list, tuple)) or len(lines) == 0:
        return CreateDraftResult(False,
            "Απαιτείται τουλάχιστον μία γραμμή παραλαβής.")
    if any(not isinstance(li, dict) for li in lines):
        return CreateDraftResult(False, "Κάθε γραμμή πρέπει να είναι λεξικό.")

    # Validate every line before any numeric comparison
    for i, li in enumerate(lines):
        err = _validate_receipt_line(li, i)
        if err:
            return CreateDraftResult(False, err)

    has_qty = any(li.get("received_qty", 0) > 0 for li in lines)
    if not has_qty:
        return CreateDraftResult(False,
            "Τουλάχιστον μία γραμμή πρέπει να έχει ποσότητα > 0.")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not received_at:
        received_at = date.today().isoformat()
    else:
        try:
            date.fromisoformat(received_at)
        except (ValueError, TypeError):
            return CreateDraftResult(False,
                f"Μη έγκυρη ημερομηνία παραλαβής '{received_at}'. YYYY-MM-DD.")

    receipt_id = "GR-" + uuid.uuid4().hex
    doc_type_label = {"delivery_note": "delivery_note",
                      "supplier_invoice": "supplier_invoice"}[document_type]

    conn = None
    try:
        conn = _get_conn(db_path)
        ensure_goods_receipt_schema(conn)

        # Verify supplier exists
        sup = conn.execute(
            "SELECT id, name FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if sup is None:
            return CreateDraftResult(False,
                f"Δεν βρέθηκε προμηθευτής με ID {supplier_id}.")

        # Insert receipt header
        try:
            conn.execute(
                """INSERT INTO goods_receipts
                   (id, supplier_id, document_number, document_type, status,
                    received_at, notes, created_at)
                   VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
                (receipt_id, supplier_id, document_number.strip(),
                 doc_type_label, received_at, notes, now))
        except sqlite3.IntegrityError:
            return CreateDraftResult(False,
                f"Υπάρχει ήδη παραστατικό '{document_number}' για τον προμηθευτή αυτόν.")

        # Insert lines
        for i, li in enumerate(lines):
            qty = int(li["received_qty"])
            cost = float(li["unit_cost"])
            batch = str(li.get("batch_number", "")).strip()
            expiry = str(li.get("expiry_date", "")).strip()
            conn.execute(
                """INSERT INTO goods_receipt_items
                   (receipt_id, line_number, barcode, product_name,
                    received_qty, unit_cost, batch_number, expiry_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, i + 1, li["barcode"].strip(),
                 li["product_name"].strip(), qty, cost, batch, expiry))

        conn.commit()
        return CreateDraftResult(True, receipt_id=receipt_id)

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return CreateDraftResult(False, f"Σφάλμα δημιουργίας πρόχειρης παραλαβής: {e}")
    finally:
        if conn:
            conn.close()


def get_receipt(db_path: str, receipt_id: str) -> GetReceiptResult:
    """Fetch a single receipt with its lines."""
    conn = None
    try:
        conn = _get_conn(db_path)
        ensure_goods_receipt_schema(conn)

        row = conn.execute(
            """SELECT r.id, r.supplier_id, COALESCE(s.name, 'Άγνωστος') AS supplier_name,
                      r.document_number, r.document_type, r.status,
                      r.received_at, r.approved_at, r.approved_by,
                      r.notes, r.created_at
               FROM goods_receipts r
               LEFT JOIN suppliers s ON s.id = r.supplier_id
               WHERE r.id = ?""",
            (receipt_id,)).fetchone()

        if row is None:
            return GetReceiptResult(False, f"Δεν βρέθηκε παραλαβή με ID '{receipt_id}'.")

        line_rows = conn.execute(
            """SELECT line_number, barcode, product_name,
                      received_qty, unit_cost, batch_number, expiry_date
               FROM goods_receipt_items
               WHERE receipt_id = ?
               ORDER BY line_number""",
            (receipt_id,)).fetchall()

        lines = tuple(GoodsReceiptLine(
            line_number=r["line_number"],
            barcode=r["barcode"],
            product_name=r["product_name"],
            received_qty=r["received_qty"],
            unit_cost=r["unit_cost"],
            batch_number=r["batch_number"] or "",
            expiry_date=r["expiry_date"] or "",
        ) for r in line_rows)

        return GetReceiptResult(True, receipt=GoodsReceipt(
            id=row["id"],
            supplier_id=row["supplier_id"],
            supplier_name=row["supplier_name"],
            document_number=row["document_number"],
            document_type=row["document_type"],
            status=row["status"],
            received_at=row["received_at"],
            approved_at=row["approved_at"],
            approved_by=row["approved_by"],
            notes=row["notes"],
            created_at=row["created_at"],
            lines=lines,
        ))

    except Exception as e:
        return GetReceiptResult(False, f"Σφάλμα ανάκτησης παραλαβής: {e}")
    finally:
        if conn:
            conn.close()


def list_receipts(
    db_path: str,
    page: int = 1,
    page_size: int = 50,
    supplier_id: int | None = None,
    status: str | None = None,
    search_text: str = "",
) -> ReceiptListResult:
    """List receipts, newest first, with optional filters and search.

    search_text filters by supplier name or document number using
    Greek-safe accent/case-insensitive matching.
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 100)

    conn = None
    try:
        conn = _get_conn(db_path)
        ensure_goods_receipt_schema(conn)
        conn.create_function("search_normalize", 1, _normalize_search, deterministic=True)

        conditions: list[str] = []
        params: list = []

        if supplier_id is not None:
            conditions.append("r.supplier_id = ?")
            params.append(supplier_id)
        if status is not None:
            conditions.append("r.status = ?")
            params.append(status)
        if search_text.strip():
            norm = _normalize_search(search_text.strip())
            esc = _escape_like(norm)
            conditions.append(
                "(search_normalize(COALESCE(s.name, '')) LIKE ? ESCAPE '\\' "
                "OR search_normalize(r.document_number) LIKE ? ESCAPE '\\')")
            params.append(f"%{esc}%")
            params.append(f"%{esc}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        # When search_text filters by supplier name, the COUNT query needs the JOIN too
        join_clause = " LEFT JOIN suppliers s ON s.id = r.supplier_id" if search_text.strip() else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM goods_receipts r{join_clause}{where}", params).fetchone()[0]

        if total == 0:
            page = 1
        else:
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))

        offset = (page - 1) * page_size

        rows = conn.execute(
            f"""SELECT r.id, COALESCE(s.name, 'Άγνωστος') AS supplier_name,
                       r.document_number, r.document_type,
                       r.received_at, r.status
                FROM goods_receipts r
                LEFT JOIN suppliers s ON s.id = r.supplier_id
                {where}
                ORDER BY r.created_at DESC
                LIMIT ? OFFSET ?""",
            params + [page_size, offset]).fetchall()

        items = tuple(
            (r["id"], r["supplier_name"], r["document_number"],
             r["document_type"], r["received_at"], r["status"])
            for r in rows
        )

        return ReceiptListResult(True, total=total, page=page,
                                 page_size=page_size, items=items)

    except Exception as e:
        return ReceiptListResult(False, f"Σφάλμα λίστας παραλαβών: {e}")
    finally:
        if conn:
            conn.close()


def approve_receipt(
    db_path: str,
    receipt_id: str,
    operator: str = "Σύστημα",
) -> ApproveReceiptResult:
    """Approve a draft receipt — atomically increment stock and create lots.

    Runs inside one BEGIN IMMEDIATE transaction. Validates:
    - Receipt exists and status is 'draft'.
    - Supplier exists.
    - At least one line has received_qty > 0.
    - Every barcode exists in ProductMaster.

    For each line with received_qty > 0:
    - Increment ProductMaster.Stock.
    - Create or increment the matching stock_lots row.
    - Log one stock_movement audit row.

    On any failure, rolls back everything.
    """
    conn = None
    try:
        conn = _get_conn(db_path)
        ensure_goods_receipt_schema(conn)
        conn.execute("BEGIN IMMEDIATE")

        # ── Re-read receipt and validate status ─────────────────────
        rec = conn.execute(
            """SELECT id, supplier_id, status
               FROM goods_receipts WHERE id=?""",
            (receipt_id,)).fetchone()
        if rec is None:
            conn.rollback()
            return ApproveReceiptResult(False,
                f"Δεν βρέθηκε παραλαβή με ID '{receipt_id}'.")
        if rec["status"] != "draft":
            conn.rollback()
            return ApproveReceiptResult(False,
                f"Η παραλαβή έχει κατάσταση '{rec['status']}' — μόνο drafts εγκρίνονται.")

        # ── Validate supplier exists ───────────────────────────────
        sup = conn.execute(
            "SELECT id FROM suppliers WHERE id=?", (rec["supplier_id"],)).fetchone()
        if sup is None:
            conn.rollback()
            return ApproveReceiptResult(False,
                f"Ο προμηθευτής ID={rec['supplier_id']} δεν υπάρχει.")

        # ── Load lines (ordered) ────────────────────────────────────
        line_rows = conn.execute(
            """SELECT line_number, barcode, product_name,
                      received_qty, unit_cost, batch_number, expiry_date
               FROM goods_receipt_items
               WHERE receipt_id = ?
               ORDER BY line_number""",
            (receipt_id,)).fetchall()

        if not line_rows:
            conn.rollback()
            return ApproveReceiptResult(False,
                "Η παραλαβή δεν έχει γραμμές.")

        has_qty = any(r["received_qty"] > 0 for r in line_rows)
        if not has_qty:
            conn.rollback()
            return ApproveReceiptResult(False,
                "Καμία γραμμή δεν έχει ποσότητα > 0.")

        # ── Validate every barcode exists ──────────────────────────
        barcodes = {r["barcode"] for r in line_rows}
        placeholders = ",".join("?" for _ in barcodes)
        existing = set()
        for r2 in conn.execute(
            f"SELECT Barcode FROM ProductMaster WHERE Barcode IN ({placeholders})",
            tuple(barcodes),
        ).fetchall():
            existing.add(r2["Barcode"])

        missing = barcodes - existing
        if missing:
            conn.rollback()
            return ApproveReceiptResult(False,
                f"Μη γνωστά barcodes: {', '.join(sorted(missing))}. "
                f"Καταχωρήστε τα προϊόντα πρώτα.")

        # ── Process lines (only those with received_qty > 0) ────────
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines_applied = 0
        total_units = 0

        for r in line_rows:
            qty = r["received_qty"]
            if qty <= 0:
                continue

            barcode = r["barcode"]
            name = r["product_name"]

            # Read and increment ProductMaster.Stock
            pm = conn.execute(
                "SELECT Stock FROM ProductMaster WHERE Barcode=?",
                (barcode,)).fetchone()
            old_stock = int(pm["Stock"])
            new_stock = old_stock + qty
            conn.execute(
                "UPDATE ProductMaster SET Stock=? WHERE Barcode=?",
                (new_stock, barcode))

            # Upsert stock_lots
            batch = r["batch_number"] or ""
            expiry = r["expiry_date"] or ""
            lot = conn.execute(
                """SELECT id, quantity FROM stock_lots
                   WHERE barcode=? AND batch_number=? AND expiry_date=?""",
                (barcode, batch, expiry)).fetchone()
            if lot:
                conn.execute(
                    "UPDATE stock_lots SET quantity=quantity+?, updated_at=? WHERE id=?",
                    (qty, now, lot["id"]))
            else:
                conn.execute(
                    """INSERT INTO stock_lots
                       (barcode, batch_number, expiry_date, quantity, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (barcode, batch, expiry, qty, now, now))

            # Audit row
            DatabaseService._log_stock_movement_on_conn(
                conn, barcode, name,
                old_stock=old_stock, new_stock=new_stock,
                reason="Παραλαβή από προμηθευτή",
                source="Παραλαβή",
                operator=operator)

            lines_applied += 1
            total_units += qty

        # ── Update receipt status ────────────────────────────────────
        cur = conn.execute(
            """UPDATE goods_receipts
               SET status='approved', approved_at=?, approved_by=?
               WHERE id=? AND status='draft'""",
            (now, operator, receipt_id))
        if cur.rowcount != 1:
            conn.rollback()
            return ApproveReceiptResult(False,
                "Αποτυχία ενημέρωσης κατάστασης — πιθανή ταυτόχρονη τροποποίηση.")

        conn.commit()
        return ApproveReceiptResult(
            True, receipt_id=receipt_id,
            lines_applied=lines_applied, total_units=total_units)

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return ApproveReceiptResult(False, f"Σφάλμα έγκρισης: {e}")
    finally:
        if conn:
            conn.close()


def cancel_receipt(db_path: str, receipt_id: str) -> CancelReceiptResult:
    """Cancel a draft receipt.  Allowed only for drafts; writes no stock/audit rows."""
    conn = None
    try:
        conn = _get_conn(db_path)
        ensure_goods_receipt_schema(conn)
        conn.execute("BEGIN IMMEDIATE")

        rec = conn.execute(
            "SELECT id, status FROM goods_receipts WHERE id=?",
            (receipt_id,)).fetchone()
        if rec is None:
            conn.rollback()
            return CancelReceiptResult(False,
                f"Δεν βρέθηκε παραλαβή με ID '{receipt_id}'.")
        if rec["status"] != "draft":
            conn.rollback()
            return CancelReceiptResult(False,
                f"Η παραλαβή έχει κατάσταση '{rec['status']}' — μόνο drafts ακυρώνονται.")

        conn.execute(
            "UPDATE goods_receipts SET status='cancelled' WHERE id=?",
            (receipt_id,))
        conn.commit()
        return CancelReceiptResult(True, receipt_id=receipt_id)

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return CancelReceiptResult(False, f"Σφάλμα ακύρωσης: {e}")
    finally:
        if conn:
            conn.close()
