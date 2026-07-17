"""
Atomic commit of NEW products from XLSX (Phase C1).

Insert-only — never updates, overwrites, or deletes existing products.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import openpyxl
from infrastructure.product_import_preview import ImportColumnMapping
from infrastructure.product_import_conflicts import (
    _normalize_barcode, _normalize_name, _normalize_stock,
    _normalize_price, _normalize_expiry, IncomingProduct,
    _connect_ro as _conflicts_connect_ro,
)
from infrastructure.product_import_plan import ImportPlan
from infrastructure.product_import_identity import (
    verify_import_source, ImportSourceSignature,
)


@dataclass(frozen=True)
class ImportCommitResult:
    ok: bool
    cancelled: bool
    error_message: str
    inserted_rows: int
    skipped_identical: int
    skipped_changed: int
    rejected_invalid: int
    skipped_duplicates: int
    source_signature: ImportSourceSignature | None

    @classmethod
    def success(cls, inserted, skipped_id, skipped_ch, rejected, skipped_dup,
                sig):
        return cls(True, False, "", inserted, skipped_id, skipped_ch,
                   rejected, skipped_dup, sig)

    @classmethod
    def cancelled_result(cls, sig):
        return cls(False, True, "Η εισαγωγή ακυρώθηκε.",
                   0, 0, 0, 0, 0, sig)

    @classmethod
    def failure(cls, msg, sig=None):
        return cls(False, False, msg, 0, 0, 0, 0, 0, sig)


def _connect_rw(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _revalidate_stream(
    file_path, mapping, sheet_name, conn, cancel_event, max_rows,
) -> Tuple[bool, str, dict, int, int, int, int, int]:
    """Stream XLSX, validate, classify against current DB.
    Returns (ok, error_msg, incoming_dict, valid, invalid, dupes,
             new_count, identical_count, changed_count).
    """
    BATCH_SIZE = 500
    valid = invalid = dupes = 0
    new_count = identical_count = changed_count = 0
    seen: set[str] = set()
    dupe_set: set[str] = set()
    incoming: dict[str, IncomingProduct] = {}
    col_map = {}

    wb = None
    try:
        wb = openpyxl.load_workbook(
            file_path, read_only=True, data_only=True, keep_links=False)
        ws = wb[sheet_name] if sheet_name else wb.active
        active_sheet = ws.title

        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, values_only=True), start=1):

            if cancel_event and cancel_event.is_set():
                wb.close()
                return (False, "Ακυρώθηκε", {}, valid, invalid, len(dupe_set),
                        0, 0, 0)

            if row_idx == 1:
                headers = tuple(
                    str(c).strip() if c is not None else "" for c in row)
                for key, col_name in [
                    ("barcode", mapping.barcode_column),
                    ("name", mapping.name_column),
                    ("stock", mapping.stock_column),
                    ("price", mapping.price_column),
                    ("expiry", mapping.expiry_date_column)]:
                    try:
                        col_map[key] = headers.index(col_name)
                    except ValueError:
                        wb.close()
                        return (False,
                                f"Στήλη '{col_name}' δεν βρέθηκε.", {},
                                0, 0, 0, 0, 0, 0)
                continue

            if row_idx - 1 > max_rows:
                break

            def _col(key):
                idx = col_map[key]
                return row[idx] if idx < len(row) else None

            barcode = _normalize_barcode(_col("barcode"))
            name = _normalize_name(_col("name"))
            stock = _normalize_stock(_col("stock"))
            price = _normalize_price(_col("price"))
            expiry = _normalize_expiry(_col("expiry"))

            if not all([barcode, name, stock is not None, price is not None, expiry is not None]):
                invalid += 1
                continue
            if barcode in seen:
                dupe_set.add(barcode)
                dupes += 1
                continue
            seen.add(barcode)

            incoming[barcode] = IncomingProduct(
                barcode, name, stock, price, expiry or "")
            valid += 1

            # Classify in batches
            if len(incoming) >= BATCH_SIZE:
                nc, ic, cc = _classify_chunk(incoming, conn, cancel_event)
                new_count += nc
                identical_count += ic
                changed_count += cc
                incoming.clear()

        # Final batch
        if incoming:
            nc, ic, cc = _classify_chunk(incoming, conn, cancel_event)
            new_count += nc
            identical_count += ic
            changed_count += cc

        return (True, "", incoming, valid, invalid, len(dupe_set),
                new_count, identical_count, changed_count)

    except Exception as e:
        return (False, str(e), {}, 0, 0, 0, 0, 0, 0)
    finally:
        if wb:
            wb.close()


def _classify_chunk(incoming, conn, cancel_event):
    """Classify one chunk against DB. Returns (new, identical, changed)."""
    barcodes = list(incoming.keys())
    placeholders = ",".join("?" for _ in barcodes)
    rows = conn.execute(
        f"SELECT Barcode, Name, Stock, Price, ExpiryDate "
        f"FROM ProductMaster WHERE Barcode IN ({placeholders})",
        barcodes).fetchall()
    db_map = {r["Barcode"]: r for r in rows}

    new = identical = changed = 0
    for barcode in barcodes:
        if cancel_event and cancel_event.is_set():
            return new, identical, changed
        prod = incoming[barcode]
        existing = db_map.get(barcode)
        if existing is None:
            new += 1
            continue
        if (prod.name == (existing["Name"] or "") and
                prod.stock == (existing["Stock"] or 0) and
                abs(float(prod.price) - float(existing["Price"] or 0.0)) < 0.0001 and
                (prod.expiry_date or "") == (existing["ExpiryDate"] or "")):
            identical += 1
        else:
            changed += 1
    return new, identical, changed


def commit_new_products_from_xlsx(
    file_path: str,
    mapping: ImportColumnMapping,
    plan: ImportPlan,
    db_path: str,
    cancel_event=None,
) -> ImportCommitResult:
    """Atomic commit of only NEW products."""
    # Preconditions
    if not plan.read_only:
        return ImportCommitResult.failure(
            "Το σχέδιο δεν είναι ανάγνωσης μόνο.")
    if plan.source_signature is None:
        return ImportCommitResult.failure(
            "Το σχέδιο δεν έχει ταυτότητα αρχείου.")
    if plan.planned_new == 0:
        return ImportCommitResult.failure(
            "Δεν υπάρχουν νέα προϊόντα για εισαγωγή.")

    # Verify source before any DB work
    if not verify_import_source(
            plan.source_signature, file_path, mapping):
        return ImportCommitResult.failure(
            "Το αρχείο ή η αντιστοίχιση άλλαξε από τη δημιουργία "
            "του σχεδίου. Δημιουργήστε νέο σχέδιο.")

    conn = None
    try:
        conn = _connect_rw(db_path)
        conn.execute("BEGIN IMMEDIATE")

        # Re-verify inside transaction
        if not verify_import_source(
                plan.source_signature, file_path, mapping):
            conn.rollback()
            conn.close()
            return ImportCommitResult.failure(
                "Το αρχείο άλλαξε μετά το κλείδωμα. "
                "Δημιουργήστε νέο σχέδιο.")

        # Revalidate
        ok, err, incoming, valid, invalid, dupes, new_cnt, ident_cnt, chg_cnt = (
            _revalidate_stream(file_path, mapping, plan.sheet_name,
                               conn, cancel_event, 250_000))

        if cancel_event and cancel_event.is_set():
            conn.rollback()
            conn.close()
            return ImportCommitResult.cancelled_result(plan.source_signature)

        if not ok:
            conn.rollback()
            conn.close()
            return ImportCommitResult.failure(
                f"Αποτυχία επανεπικύρωσης: {err}")

        # Compare counters against plan
        changed_total = plan.manual_review + plan.skipped_changed
        if (valid != plan.valid_rows or invalid != plan.invalid_rows or
                dupes != plan.duplicate_barcodes or
                new_cnt != plan.planned_new or
                ident_cnt != plan.skipped_identical or
                chg_cnt != changed_total):
            conn.rollback()
            conn.close()
            return ImportCommitResult.failure(
                "Η βάση δεδομένων ή το αρχείο άλλαξε από τη "
                "δημιουργία του σχεδίου. Δημιουργήστε νέο σχέδιο.")

        # ---- Insert pass ---- 
        inserted = 0
        wb = None
        try:
            wb = openpyxl.load_workbook(
                file_path, read_only=True, data_only=True, keep_links=False)
            ws = wb[plan.sheet_name] if plan.sheet_name else wb.active
            col_map = {}
            seen_insert: set[str] = set()

            for row_idx, row in enumerate(
                ws.iter_rows(min_row=1, values_only=True), start=1):

                if cancel_event and cancel_event.is_set():
                    conn.rollback()
                    if wb:
                        wb.close()
                    conn.close()
                    return ImportCommitResult.cancelled_result(
                        plan.source_signature)

                if row_idx == 1:
                    headers = tuple(
                        str(c).strip() if c is not None else ""
                        for c in row)
                    for key, col_name in [
                        ("barcode", mapping.barcode_column),
                        ("name", mapping.name_column),
                        ("stock", mapping.stock_column),
                        ("price", mapping.price_column),
                        ("expiry", mapping.expiry_date_column)]:
                        try:
                            col_map[key] = headers.index(col_name)
                        except ValueError:
                            conn.rollback()
                            if wb:
                                wb.close()
                            conn.close()
                            return ImportCommitResult.failure(
                                f"Στήλη '{col_name}' δεν βρέθηκε.")
                    continue

                def _col(key):
                    idx = col_map[key]
                    return row[idx] if idx < len(row) else None

                barcode = _normalize_barcode(_col("barcode"))
                name = _normalize_name(_col("name"))
                stock = _normalize_stock(_col("stock"))
                price = _normalize_price(_col("price"))
                expiry = _normalize_expiry(_col("expiry"))

                if not all([barcode, name, stock is not None,
                            price is not None, expiry is not None]):
                    continue
                if barcode in seen_insert:
                    continue
                seen_insert.add(barcode)

                # Check: only insert if NEW (not in DB)
                existing = conn.execute(
                    "SELECT Barcode FROM ProductMaster WHERE Barcode=?",
                    (barcode,)).fetchone()
                if existing is not None:
                    continue

                # Insert product
                conn.execute(
                    "INSERT INTO ProductMaster "
                    "(Barcode, Name, Stock, ExpiryDate, Price) "
                    "VALUES (?,?,?,?,?)",
                    (barcode, name, stock, expiry, price))

                # Audit row
                conn.execute(
                    "INSERT INTO stock_movements "
                    "(barcode, product_name, old_stock, new_stock, "
                    "change_amount, reason, source, operator, timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                    (barcode, name, 0, stock, stock,
                     "Εισαγωγή Excel", "Excel Import", "system"))

                inserted += 1

        finally:
            if wb:
                wb.close()

        # Final signature check before commit
        if not verify_import_source(
                plan.source_signature, file_path, mapping):
            conn.rollback()
            conn.close()
            return ImportCommitResult.failure(
                "Το αρχείο άλλαξε πριν την ολοκλήρωση. "
                "Δημιουργήστε νέο σχέδιο.")

        conn.commit()
        conn.close()

        return ImportCommitResult.success(
            inserted, plan.skipped_identical,
            plan.manual_review + plan.skipped_changed,
            plan.rejected_invalid, plan.skipped_duplicates,
            plan.source_signature)

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
        return ImportCommitResult.failure(
            f"Σφάλμα εισαγωγής: {e}")
