"""Read-only stock-lot expiry integrity model (Phase P5.2a).

Provides a typed snapshot of how trustworthy the current stock_lots and
expiry data is, enabling operators to assess readiness before enabling
lot-aware daily alerts.

This module is READ-ONLY — it never writes to the database or calls
ensure_goods_receipt_schema().

Design conventions
------------------
* Frozen dataclasses for typed models (matches qt_app/data_source.py).
* Read-only SQLite connection via ``mode=ro`` URI.
* ``business_date`` is an explicit parameter — no SQLite ``date('now')``
  or ``datetime.today()``.
* Date validation via regex + ``date.fromisoformat()`` — strict YYYY-MM-DD.
* Blank expiry dates are "undated", not invalid.
* stock_lots rows with ``quantity == 0`` are ignored.
* If the ``stock_lots`` table does not exist, returns a successful typed
  snapshot with ``tracking.available == False``.
* No dependency on ``ProductMaster.ExpiryDate``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import List, Tuple


# ═══════════════════════════════════════════════════════════════════════
# Date validation
# ═══════════════════════════════════════════════════════════════════════

_DATE_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")


def _validate_date_str(value: str) -> date | None:
    """Return a ``date`` if *value* is canonical YYYY-MM-DD, else ``None``.

    Rejects out-of-range months/days (e.g. 2026-02-30) that
    ``date.fromisoformat`` silently normalises in Python 3.11.
    """
    if not _DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# Typed models
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProductLotIntegrity:
    """Per-product stock-lot integrity row (immutable snapshot)."""

    barcode: str
    product_name: str
    master_stock: int
    total_lot_qty: int
    qty_in_dated_lots: int
    qty_in_undated_lots: int
    qty_in_invalid_date_lots: int
    expired_lot_qty: int
    expiring_soon_lot_qty: int
    future_lot_qty: int
    earliest_valid_expiry: str  # "—" when no valid expiry exists
    untracked_qty: int          # max(master_stock - total_lot_qty, 0)
    lot_overage_qty: int        # max(total_lot_qty - master_stock, 0)
    status: str                 # Deterministic Greek label
    status_reason: str          # Deterministic Greek explanation

    # Severity priority (0 = highest priority, 6 = fully tracked)
    STATUS_LEVELS = {
        "Λάθος: Υπερβολική Ποσότητα Παρτίδας": 0,
        "Ληγμένο": 1,
        "Μη Έγκυρη Ημερομηνία": 2,
        "Λήγει Σύντομα": 3,
        "Απαρακολούθητο Απόθεμα": 4,
        "Αχρονολόγητες Παρτίδες": 5,
        "Πλήρως Καταγεγραμμένο": 6,
    }

    def severity_key(self) -> tuple:
        """Deterministic sort key: severity → earliest_expiry → name → barcode."""
        level = self.STATUS_LEVELS.get(self.status, 99)
        exp = self.earliest_valid_expiry
        if exp == "—" or _validate_date_str(exp) is None:
            exp_key = "9999-99-99"
        else:
            exp_key = exp
        return (level, exp_key, self.product_name, self.barcode)


@dataclass(frozen=True)
class LotTrackingAvailability:
    """Whether stock_lot tracking is available in the database."""
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class StockLotIntegritySnapshot:
    """Aggregate snapshot of stock-lot tracking readiness."""

    per_product: Tuple[ProductLotIntegrity, ...]
    total_products_with_stock: int
    fully_covered: int
    untracked_products: int
    undated_lot_products: int
    invalid_date_products: int
    lot_overage_products: int
    expired_lot_units: int
    expiring_soon_lot_units: int
    tracking: LotTrackingAvailability

    @classmethod
    def unavailable(cls) -> StockLotIntegritySnapshot:
        """Return a safe snapshot when stock_lots table does not exist."""
        return cls(
            per_product=(),
            total_products_with_stock=0,
            fully_covered=0,
            untracked_products=0,
            undated_lot_products=0,
            invalid_date_products=0,
            lot_overage_products=0,
            expired_lot_units=0,
            expiring_soon_lot_units=0,
            tracking=LotTrackingAvailability(
                available=False,
                reason="Η παρακολούθηση παρτίδων (stock_lots) δεν είναι διαθέσιμη. "
                       "Ο πίνακας stock_lots δεν υπάρχει στη βάση δεδομένων.",
            ),
        )


@dataclass(frozen=True)
class StockLotIntegrityResult:
    """Carries either a successful snapshot or a Greek error message."""
    ok: bool
    snapshot: StockLotIntegritySnapshot | None = None
    error_message: str = ""

    @classmethod
    def success(cls, snapshot: StockLotIntegritySnapshot) -> StockLotIntegrityResult:
        return cls(ok=True, snapshot=snapshot)

    @classmethod
    def failure(cls, message: str) -> StockLotIntegrityResult:
        return cls(ok=False, error_message=message)


# ═══════════════════════════════════════════════════════════════════════
# Connection helper (read-only)
# ═══════════════════════════════════════════════════════════════════════

def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection via URI mode=ro.

    Preserves WAL compatibility and never writes to the database.
    """
    path = db_path.replace("\\", "/")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(cur, name: str) -> bool:
    """Check if a table exists (read-only PRAGMA introspection)."""
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


# ═══════════════════════════════════════════════════════════════════════
# Status label helpers
# ═══════════════════════════════════════════════════════════════════════

def _classify_product(
    master_stock: int,
    total_lot: int,
    qty_dated: int,
    qty_undated: int,
    qty_invalid: int,
    qty_expired: int,
    qty_expiring: int,
    qty_future: int,
    lot_overage: int,
) -> tuple[str, str]:
    """Return (status, reason) — deterministic Greek labels.

    Priority (highest to lowest):
      1. Lot quantity exceeds master stock  (data integrity error)
      2. Expired positive lot quantity
      3. Invalid lot dates
      4. Expiring-soon positive lot quantity
      5. Untracked master stock
      6. Undated lot quantity
      7. Fully tracked
    """
    if lot_overage > 0:
        return (
            "Λάθος: Υπερβολική Ποσότητα Παρτίδας",
            f"Η συνολική ποσότητα παρτίδων ({total_lot}) "
            f"υπερβαίνει το απόθεμα του προϊόντος ({master_stock}).",
        )
    if qty_expired > 0:
        return (
            "Ληγμένο",
            f"{qty_expired} μονάδες σε ληγμένες παρτίδες.",
        )
    if qty_invalid > 0:
        return (
            "Μη Έγκυρη Ημερομηνία",
            f"{qty_invalid} μονάδες σε παρτίδες με μη έγκυρη ημερομηνία λήξης.",
        )
    if qty_expiring > 0:
        return (
            "Λήγει Σύντομα",
            f"{qty_expiring} μονάδες σε παρτίδες που λήγουν σύντομα.",
        )
    if master_stock > total_lot:
        return (
            "Απαρακολούθητο Απόθεμα",
            f"Οι παρτίδες καλύπτουν {total_lot} από {master_stock} μονάδες.",
        )
    if qty_undated > 0:
        return (
            "Αχρονολόγητες Παρτίδες",
            f"{qty_undated} μονάδες σε παρτίδες χωρίς ημερομηνία λήξης.",
        )
    return (
        "Πλήρως Καταγεγραμμένο",
        f"Όλες οι {master_stock} μονάδες καλύπτονται από παρτίδες με έγκυρη ημερομηνία.",
    )


# ═══════════════════════════════════════════════════════════════════════
# Public query entry point
# ═══════════════════════════════════════════════════════════════════════

def load_stock_lot_integrity(
    db_path: str,
    business_date: str,
    alert_days: int = 30,
) -> StockLotIntegrityResult:
    """Return a typed snapshot of stock-lot tracking integrity.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    business_date : str
        Explicit reference date in YYYY-MM-DD format.  **Not** derived
        from ``date('now')`` or ``datetime.today()``.
    alert_days : int
        Number of days from ``business_date`` to define "expiring soon".
        Default 30.

    Returns
    -------
    StockLotIntegrityResult
        ``.ok == True`` with a ``.snapshot`` (unavailable table returns
        a valid snapshot with ``tracking.available == False``).
        ``.ok == False`` with a Greek error message on real failures.

    Read-only contract
    ------------------
    * Does NOT call ``ensure_goods_receipt_schema()``.
    * Does NOT create or migrate tables.
    * Does NOT modify any row.
    * Opens SQLite in read-only mode (``mode=ro`` URI).
    """
    # ── Validate business_date ──────────────────────────────────────
    bd = _validate_date_str(business_date)
    if bd is None:
        return StockLotIntegrityResult.failure(
            f"Μη έγκυρη ημερομηνία αναφοράς: '{business_date}'. "
            "Απαιτείται YYYY-MM-DD."
        )

    if not isinstance(alert_days, int) or alert_days < 0:
        return StockLotIntegrityResult.failure(
            f"Το alert_days πρέπει να είναι μη αρνητικός ακέραιος, "
            f"όχι '{alert_days}'."
        )

    # Compute cutoff: business_date + alert_days
    cutoff = date.fromordinal(bd.toordinal() + alert_days)
    bd_str = bd.isoformat()
    cutoff_str = cutoff.isoformat()

    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()

        # ── Check if stock_lots exists (read-only PRAGMA) ───────────
        if not _has_table(cur, "stock_lots"):
            return StockLotIntegrityResult.success(
                StockLotIntegritySnapshot.unavailable()
            )

        # ── Single read-only query: ProductMaster LEFT JOIN aggregated lots ──
        rows = cur.execute(
            f"""
            SELECT
                p.Barcode,
                p.Name,
                p.Stock,
                COALESCE(lot.total_qty, 0)             AS total_lot_qty,
                COALESCE(lot.dated_qty, 0)              AS dated_qty,
                COALESCE(lot.undated_qty, 0)            AS undated_qty,
                COALESCE(lot.invalid_date_qty, 0)       AS invalid_date_qty,
                COALESCE(lot.expired_qty, 0)            AS expired_qty,
                COALESCE(lot.expiring_qty, 0)           AS expiring_qty,
                COALESCE(lot.future_qty, 0)             AS future_qty,
                COALESCE(lot.earliest_valid, '')         AS earliest_valid_expiry
            FROM ProductMaster p
            LEFT JOIN (
                SELECT
                    sl.barcode,
                    SUM(sl.quantity)                                        AS total_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND date(sl.expiry_date) = sl.expiry_date
                              THEN sl.quantity ELSE 0 END)                 AS dated_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date = ''
                              THEN sl.quantity ELSE 0 END)                 AS undated_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND (date(sl.expiry_date) IS NULL
                                   OR date(sl.expiry_date) != sl.expiry_date)
                              THEN sl.quantity ELSE 0 END)                 AS invalid_date_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND date(sl.expiry_date) = sl.expiry_date
                              AND date(sl.expiry_date) < ?
                              THEN sl.quantity ELSE 0 END)                 AS expired_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND date(sl.expiry_date) = sl.expiry_date
                              AND date(sl.expiry_date) >= ?
                              AND date(sl.expiry_date) <= ?
                              THEN sl.quantity ELSE 0 END)                 AS expiring_qty,
                    SUM(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND date(sl.expiry_date) = sl.expiry_date
                              AND date(sl.expiry_date) > ?
                              THEN sl.quantity ELSE 0 END)                 AS future_qty,
                    MIN(CASE WHEN sl.quantity > 0
                              AND sl.expiry_date != ''
                              AND date(sl.expiry_date) = sl.expiry_date
                              THEN sl.expiry_date END)                     AS earliest_valid
                FROM stock_lots sl
                WHERE sl.quantity > 0
                GROUP BY sl.barcode
            ) lot ON lot.barcode = p.Barcode
            WHERE p.Stock > 0
               OR COALESCE(lot.total_qty, 0) > 0
            ORDER BY p.Stock DESC, p.Name ASC
            """,
            (bd_str, bd_str, cutoff_str, cutoff_str),
        ).fetchall()

        per_product: List[ProductLotIntegrity] = []
        for r in rows:
            barcode: str = r["Barcode"]
            name: str = r["Name"]
            ms = int(r["Stock"])
            tq = int(r["total_lot_qty"])
            dq = int(r["dated_qty"])
            ud = int(r["undated_qty"])
            iv = int(r["invalid_date_qty"])
            eq = int(r["expired_qty"])
            es = int(r["expiring_qty"])
            fq = int(r["future_qty"])
            ev_raw = r["earliest_valid_expiry"] or ""
            ev = ev_raw if ev_raw and _validate_date_str(ev_raw) else "—"

            untracked = max(ms - tq, 0)
            overage = max(tq - ms, 0)

            status, reason = _classify_product(
                ms, tq, dq, ud, iv, eq, es, fq, overage,
            )

            per_product.append(ProductLotIntegrity(
                barcode=barcode,
                product_name=name,
                master_stock=ms,
                total_lot_qty=tq,
                qty_in_dated_lots=dq,
                qty_in_undated_lots=ud,
                qty_in_invalid_date_lots=iv,
                expired_lot_qty=eq,
                expiring_soon_lot_qty=es,
                future_lot_qty=fq,
                earliest_valid_expiry=ev,
                untracked_qty=untracked,
                lot_overage_qty=overage,
                status=status,
                status_reason=reason,
            ))

        # ── Deterministic final sort ────────────────────────────────
        per_product.sort(key=lambda pi: pi.severity_key())

        # ── Aggregate totals ────────────────────────────────────────
        total_with_stock = sum(1 for p in per_product if p.master_stock > 0)
        fully_covered = sum(
            1 for p in per_product if p.status == "Πλήρως Καταγεγραμμένο"
        )
        untracked_products = sum(
            1 for p in per_product if p.status == "Απαρακολούθητο Απόθεμα"
        )
        undated_lot_products = sum(
            1 for p in per_product if p.status == "Αχρονολόγητες Παρτίδες"
        )
        invalid_date_products = sum(
            1 for p in per_product if p.status == "Μη Έγκυρη Ημερομηνία"
        )
        lot_overage_products = sum(
            1 for p in per_product if p.status == "Λάθος: Υπερβολική Ποσότητα Παρτίδας"
        )
        expired_units = sum(p.expired_lot_qty for p in per_product)
        expiring_units = sum(p.expiring_soon_lot_qty for p in per_product)

        snapshot = StockLotIntegritySnapshot(
            per_product=tuple(per_product),
            total_products_with_stock=total_with_stock,
            fully_covered=fully_covered,
            untracked_products=untracked_products,
            undated_lot_products=undated_lot_products,
            invalid_date_products=invalid_date_products,
            lot_overage_products=lot_overage_products,
            expired_lot_units=expired_units,
            expiring_soon_lot_units=expiring_units,
            tracking=LotTrackingAvailability(available=True, reason=""),
        )

        return StockLotIntegrityResult.success(snapshot)

    except FileNotFoundError:
        return StockLotIntegrityResult.failure(
            "Αδυναμία φόρτωσης δεδομένων ακεραιότητας παρτίδων: "
            f"το αρχείο βάσης δεδομένων δεν βρέθηκε ({db_path})"
        )
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "unable to open" in msg.lower() or "no such" in msg.lower():
            return StockLotIntegrityResult.failure(
                "Αδυναμία φόρτωσης δεδομένων ακεραιότητας παρτίδων: "
                f"αδυναμία ανοίγματος βάσης ({db_path})"
            )
        return StockLotIntegrityResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων ακεραιότητας παρτίδων: "
            f"σφάλμα SQLite — {msg}"
        )
    except sqlite3.DatabaseError as e:
        return StockLotIntegrityResult.failure(
            "Αδυναμία φόρτωσης δεδομένων ακεραιότητας παρτίδων: "
            f"σφάλμα βάσης δεδομένων — {e}"
        )
    except Exception as e:
        return StockLotIntegrityResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων ακεραιότητας παρτίδων: {e}"
        )
    finally:
        if conn:
            conn.close()
