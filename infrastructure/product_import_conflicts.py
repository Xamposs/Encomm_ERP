"""
Read-only database conflict analysis for XLSX product imports (Phase B1).

Streams an XLSX, validates every row, collects unique valid barcodes,
then batches database lookups against ProductMaster.  No writes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as dt_date, datetime as dt_datetime
from math import isfinite
from typing import Tuple, Dict, List, Set

import openpyxl
from infrastructure.product_import_preview import ImportColumnMapping


# ── Typed result models ──────────────────────────────────────────────


@dataclass(frozen=True)
class IncomingProduct:
    barcode: str
    name: str
    stock: int
    price: float
    expiry_date: str


@dataclass(frozen=True)
class ExistingProduct:
    barcode: str
    name: str
    stock: int
    price: float
    expiry_date: str


@dataclass(frozen=True)
class ConflictRecord:
    barcode: str
    changed_fields: Tuple[str, ...]


@dataclass(frozen=True)
class ImportRowErr:
    row_number: int
    barcode: str
    code: str
    message: str


@dataclass(frozen=True)
class ImportConflictResult:
    ok: bool
    cancelled: bool
    error_message: str = ""
    file_name: str = ""
    sheet_name: str = ""
    scanned_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    duplicate_barcodes: int = 0
    new_barcodes: int = 0
    unchanged_existing: int = 0
    changed_existing: int = 0
    conflict_samples: Tuple[ConflictRecord, ...] = ()
    errors: Tuple[ImportRowErr, ...] = ()
    sample_rows: Tuple[Tuple[str, str, int, float, str], ...] = ()

    @classmethod
    def _make(cls, ok, cancelled, msg, fn, sn, scanned, valid, invalid,
              dupes, new, unchanged, changed, conflicts, errors, samples):
        return cls(
            ok=ok, cancelled=cancelled, error_message=msg,
            file_name=fn, sheet_name=sn,
            scanned_rows=scanned, valid_rows=valid,
            invalid_rows=invalid, duplicate_barcodes=dupes,
            new_barcodes=new, unchanged_existing=unchanged,
            changed_existing=changed,
            conflict_samples=tuple(conflicts),
            errors=tuple(errors),
            sample_rows=tuple(samples))

    @classmethod
    def success(cls, fn, sn, scanned, valid, invalid, dupes, new,
                unchanged, changed, conflicts, errors, samples):
        return cls._make(True, False, "", fn, sn, scanned, valid, invalid,
                         dupes, new, unchanged, changed, conflicts, errors,
                         samples)

    @classmethod
    def cancelled(cls, fn, sn, scanned, valid, invalid, dupes, new,
                  unchanged, changed, conflicts, errors, samples):
        return cls._make(False, True, "Η ανάλυση ακυρώθηκε.",
                         fn, sn, scanned, valid, invalid, dupes, new,
                         unchanged, changed, conflicts, errors, samples)

    @classmethod
    def failure(cls, fn, sn, msg):
        return cls._make(False, False, msg, fn, sn, 0, 0, 0, 0, 0, 0, 0,
                         (), (), ())


# ── Internal helpers ──────────────────────────────────────────────────


def _connect_ro(db_path: str) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_barcode(raw) -> str | None:
    """Return barcode string or None (invalid)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return s if s else None
    if isinstance(raw, (int, float)):
        if not isfinite(raw):
            return None
        if isinstance(raw, float) and raw != int(raw):
            return None
        s = str(int(raw))
        return s if len(s) <= 15 else None
    return None


def _normalize_name(raw) -> str | None:
    if isinstance(raw, str):
        s = raw.strip()
        return s if s else None
    return None


def _normalize_stock(raw) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int) and raw >= 0:
        return raw
    if isinstance(raw, float) and isfinite(raw):
        if raw == int(raw):
            s = int(raw)
            return s if s >= 0 else None
    return None


def _normalize_price(raw) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        p = float(raw)
        if p >= 0 and isfinite(p):
            return p
    return None


def _normalize_expiry(raw) -> str | None:
    """Return ISO date string, empty string for blank, or None for invalid."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ""
    if isinstance(raw, (dt_date, dt_datetime)):
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, str):
        s = raw.strip()
        try:
            dt_date.fromisoformat(s)
            return s
        except ValueError:
            return None
    return None


# ── Public API ───────────────────────────────────────────────────────


def analyze_import_conflicts(
    file_path: str,
    mapping: ImportColumnMapping,
    db_path: str,
    sheet_name: str | None = None,
    cancel_event=None,
    max_rows: int = 250_000,
) -> ImportConflictResult:
    MAX_ERRORS = 200
    MAX_SAMPLES = 20
    MAX_CONFLICT_SAMPLES = 50
    BATCH_SIZE = 500

    wb = None
    try:
        wb = openpyxl.load_workbook(
            file_path, read_only=True, data_only=True, keep_links=False)
    except Exception as e:
        return ImportConflictResult.failure(
            file_path, sheet_name or "—",
            f"Αδυναμία ανοίγματος αρχείου: {e}")

    try:
        ws = wb[sheet_name] if sheet_name else wb.active
        active_sheet = ws.title

        col_map: dict[str, int] = {}
        incoming: Dict[str, IncomingProduct] = {}  # barcode → validated data
        seen_barcodes: Set[str] = set()
        dupe_barcodes: Set[str] = set()
        errors: List[ImportRowErr] = []
        samples: List[Tuple[str, str, int, float, str]] = []
        scanned = 0
        valid = 0
        invalid = 0
        cancelled = False

        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, values_only=True), start=1):

            if cancel_event and cancel_event.is_set():
                cancelled = True
                break

            if row_idx == 1:
                headers = tuple(
                    str(c).strip() if c is not None else "" for c in row)
                if len(set(headers)) != len(headers):
                    wb.close()
                    return ImportConflictResult.failure(
                        file_path, active_sheet,
                        "Η κεφαλίδα περιέχει διπλότυπα ονόματα στηλών.")
                for key, col_name in [
                    ("barcode", mapping.barcode_column),
                    ("name", mapping.name_column),
                    ("stock", mapping.stock_column),
                    ("price", mapping.price_column),
                    ("expiry", mapping.expiry_date_column),
                ]:
                    try:
                        col_map[key] = headers.index(col_name)
                    except ValueError:
                        wb.close()
                        return ImportConflictResult.failure(
                            file_path, active_sheet,
                            f"Στήλη '{col_name}' δεν βρέθηκε.")
                continue

            if scanned >= max_rows:
                if len(errors) < MAX_ERRORS:
                    errors.append(ImportRowErr(
                        row_idx, "", "ROWS",
                        f"Υπέρβαση ορίου {max_rows:,} γραμμών."))
                wb.close()
                return ImportConflictResult.failure(
                    file_path, active_sheet,
                    f"Υπέρβαση ορίου {max_rows:,} γραμμών.")

            scanned += 1

            def _col(key):
                idx = col_map[key]
                return row[idx] if idx < len(row) else None

            # Validate
            barcode = _normalize_barcode(_col("barcode"))
            name = _normalize_name(_col("name"))
            stock = _normalize_stock(_col("stock"))
            price = _normalize_price(_col("price"))
            expiry = _normalize_expiry(_col("expiry"))

            row_errs: list[str] = []
            if barcode is None:
                row_errs.append("Μη έγκυρο barcode.")
            if name is None:
                row_errs.append("Κενό όνομα.")
            if stock is None:
                row_errs.append("Μη έγκυρο απόθεμα.")
            if price is None:
                row_errs.append("Μη έγκυρη τιμή.")
            if expiry is None:
                row_errs.append("Μη έγκυρη ημ. λήξης.")

            if row_errs:
                invalid += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(ImportRowErr(
                        row_idx, barcode or "", "VALIDATION",
                        " · ".join(row_errs)))
                continue

            # Duplicate in file
            if barcode in seen_barcodes:
                dupe_barcodes.add(barcode)
                invalid += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(ImportRowErr(
                        row_idx, barcode,
                        "DUPLICATE",
                        f"Διπλότυπο barcode: {barcode}"))
                continue

            seen_barcodes.add(barcode)
            assert name is not None and stock is not None and price is not None

            prod = IncomingProduct(barcode, name, stock, price,
                                   expiry if expiry is not None else "")
            incoming[barcode] = prod
            valid += 1
            if len(samples) < MAX_SAMPLES:
                samples.append(
                    (barcode, name, stock, price,
                     expiry if expiry is not None else ""))

        if cancelled:
            wb.close()
            return ImportConflictResult.cancelled(
                file_path, active_sheet, scanned, valid, invalid,
                len(dupe_barcodes), 0, 0, 0, [], list(errors), samples)

    except Exception as e:
        return ImportConflictResult.failure(
            file_path, sheet_name or "—",
            f"Σφάλμα κατά την ανάγνωση: {e}")
    finally:
        if wb:
            wb.close()

    # ── Database comparison ──────────────────────────────────────────
    conn = None
    try:
        conn = _connect_ro(db_path)

        # Verify ProductMaster schema
        info = conn.execute(
            "SELECT name FROM pragma_table_info('ProductMaster')").fetchall()
        existing_cols = {r["name"] for r in info}
        for col in ["Barcode", "Name", "Stock", "Price", "ExpiryDate"]:
            if col not in existing_cols:
                conn.close()
                return ImportConflictResult.failure(
                    file_path, active_sheet,
                    f"Λείπει η στήλη {col} από τον ProductMaster.")

        barcode_list = list(incoming.keys())
        new_count = 0
        unchanged_count = 0
        changed_count = 0
        conflicts: List[ConflictRecord] = []

        for i in range(0, len(barcode_list), BATCH_SIZE):
            if cancel_event and cancel_event.is_set():
                conn.close()
                return ImportConflictResult.cancelled(
                    file_path, active_sheet, scanned, valid, invalid,
                    len(dupe_barcodes), new_count, unchanged_count,
                    changed_count, conflicts, errors, samples)

            batch = barcode_list[i:i + BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"SELECT Barcode, Name, Stock, Price, ExpiryDate "
                f"FROM ProductMaster WHERE Barcode IN ({placeholders})",
                batch).fetchall()

            db_data: Dict[str, ExistingProduct] = {}
            for r in rows:
                db_data[r["Barcode"]] = ExistingProduct(
                    barcode=r["Barcode"],
                    name=r["Name"] or "",
                    stock=r["Stock"] or 0,
                    price=r["Price"] or 0.0,
                    expiry_date=r["ExpiryDate"] or "",
                )

            for barcode in batch:
                incoming_prod = incoming[barcode]
                existing = db_data.get(barcode)

                if existing is None:
                    new_count += 1
                    continue

                # Compare
                changed: list[str] = []
                if incoming_prod.name != existing.name:
                    changed.append("Name")
                if incoming_prod.stock != existing.stock:
                    changed.append("Stock")
                if abs(incoming_prod.price - existing.price) > 0.001:
                    changed.append("Price")
                if incoming_prod.expiry_date != existing.expiry_date:
                    changed.append("ExpiryDate")

                if changed:
                    changed_count += 1
                    if len(conflicts) < MAX_CONFLICT_SAMPLES:
                        conflicts.append(
                            ConflictRecord(barcode, tuple(changed)))
                else:
                    unchanged_count += 1

        conn.close()
        conn = None

        return ImportConflictResult.success(
            file_path, active_sheet, scanned, valid, invalid,
            len(dupe_barcodes), new_count, unchanged_count, changed_count,
            conflicts, errors, samples)

    except sqlite3.Error as e:
        if conn:
            conn.close()
        return ImportConflictResult.failure(
            file_path, active_sheet,
            f"Σφάλμα βάσης δεδομένων: {e}")
    except Exception as e:
        if conn:
            conn.close()
        return ImportConflictResult.failure(
            file_path, active_sheet,
            f"Σφάλμα ανάλυσης: {e}")
