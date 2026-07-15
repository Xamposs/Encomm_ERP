"""Read-only SQLite data source for the Qt application shell.

Opens **one** read-only connection per ``load_dashboard()`` call
(``mode=ro`` via URI).  NO additional connections are opened inside
loops — all expiry logic is pushed into the critical-products SELECT
as computed ``is_expired`` / ``is_near_expiry`` flags.

Typed result contract
---------------------
``DashboardResult`` carries either a ``DashboardSnapshot`` (``.ok``) or
a Greek error message (``.ok == False``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Tuple


# ── Typed result / snapshot ────────────────────────────────────────────

@dataclass(frozen=True)
class CriticalProduct:
    """Single critical-product row (immutable snapshot)."""
    barcode: str
    name: str
    stock: int
    expiry_date: str
    price: float
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class DashboardSnapshot:
    """Immutable snapshot of all dashboard data at a point in time."""
    total_products: int
    low_stock_count: int
    expiry_alert_count: int
    revenue_today: float
    vat_today: float
    invoice_count: int
    critical_products: Tuple[CriticalProduct, ...]


@dataclass(frozen=True)
class DashboardResult:
    """Carries either a successful snapshot or a Greek error message."""
    ok: bool
    snapshot: DashboardSnapshot | None = None
    error_message: str = ""

    @classmethod
    def success(cls, snapshot: DashboardSnapshot) -> "DashboardResult":
        return cls(ok=True, snapshot=snapshot)

    @classmethod
    def failure(cls, message: str) -> "DashboardResult":
        return cls(ok=False, error_message=message)


# ── Internal helpers ───────────────────────────────────────────────────

def _connect_ro(db_path: str) -> sqlite3.Connection:
    path = db_path.replace("\\", "/")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _build_reasons_from_flags(
    is_expired: int, is_near_expiry: int,
    stock: int, threshold: int,
    expiry_date: str, alert_days: int,
) -> Tuple[str, ...]:
    """Pure-Python reason builder — NO SQLite calls.

    Flags come directly from the critical-products SELECT (0/1).
    """
    reasons: List[str] = []
    if is_expired:
        reasons.append("Ληγμένο")
    elif is_near_expiry:
        reasons.append(f"Λήγει σύντομα ({expiry_date})")
    if stock <= threshold:
        reasons.append("Χαμηλό απόθεμα")
    if not reasons:
        reasons.append("—")
    return tuple(reasons)


# ── Public query entry point ───────────────────────────────────────────

def load_dashboard(
    db_path: str,
    threshold: int = 10,
    alert_days: int = 30,
    critical_limit: int = 20,
) -> DashboardResult:
    """Run all three dashboard queries in **one** read-only transaction.

    Returns ``DashboardResult`` — on failure, ``.ok`` is False and
    ``.error_message`` contains a Greek description.
    """
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()

        # ── Counts ──
        total = cur.execute(
            "SELECT COUNT(*) FROM ProductMaster"
        ).fetchone()[0]
        low_stock = cur.execute(
            "SELECT COUNT(*) FROM ProductMaster WHERE Stock <= ?",
            (threshold,),
        ).fetchone()[0]
        expiry = cur.execute(
            "SELECT COUNT(*) FROM ProductMaster "
            "WHERE ExpiryDate != '' AND ("
            "  date(ExpiryDate) < date('now') "
            "  OR date(ExpiryDate) <= date('now', '+' || ? || ' days'))",
            (alert_days,),
        ).fetchone()[0]

        # ── Analytics ──
        revenue_today = cur.execute(
            "SELECT COALESCE(SUM(grand_total), 0) FROM invoices "
            "WHERE date(invoice_date) = date('now')"
        ).fetchone()[0]
        vat_today = cur.execute(
            "SELECT COALESCE(SUM(vat_amount), 0) FROM invoices "
            "WHERE date(invoice_date) = date('now')"
        ).fetchone()[0]
        invoice_count = cur.execute(
            "SELECT COUNT(*) FROM invoices"
        ).fetchone()[0]

        # ── Critical products (flags computed in SQL — zero extra connections) ──
        cur.execute(
            """
            SELECT Barcode, Name, Stock, ExpiryDate, Price,
                   CASE
                       WHEN ExpiryDate != '' AND date(ExpiryDate) < date('now')
                           THEN 1
                       WHEN ExpiryDate != '' AND date(ExpiryDate) <=
                            date('now', '+' || ? || ' days')
                           THEN 2
                       ELSE 3
                   END AS severity,
                   CASE WHEN ExpiryDate != ''
                             AND date(ExpiryDate) < date('now')
                        THEN 1 ELSE 0 END AS is_expired,
                   CASE WHEN ExpiryDate != ''
                             AND date(ExpiryDate) >= date('now')
                             AND date(ExpiryDate) <=
                                 date('now', '+' || ? || ' days')
                        THEN 1 ELSE 0 END AS is_near_expiry
            FROM ProductMaster
            WHERE Stock <= ?
               OR (ExpiryDate != '' AND date(ExpiryDate) < date('now'))
               OR (ExpiryDate != '' AND date(ExpiryDate) <=
                   date('now', '+' || ? || ' days'))
            ORDER BY severity ASC, ExpiryDate ASC
            LIMIT ?
            """,
            (alert_days, alert_days, threshold, alert_days, critical_limit),
        )
        crit: List[CriticalProduct] = []
        for row in cur.fetchall():
            reasons = _build_reasons_from_flags(
                is_expired=row["is_expired"],
                is_near_expiry=row["is_near_expiry"],
                stock=row["Stock"],
                threshold=threshold,
                expiry_date=row["ExpiryDate"] or "—",
                alert_days=alert_days,
            )
            crit.append(CriticalProduct(
                barcode=row["Barcode"],
                name=row["Name"],
                stock=row["Stock"],
                expiry_date=row["ExpiryDate"] or "—",
                price=row["Price"],
                reasons=reasons,
            ))

        snapshot = DashboardSnapshot(
            total_products=total,
            low_stock_count=low_stock,
            expiry_alert_count=expiry,
            revenue_today=revenue_today,
            vat_today=vat_today,
            invoice_count=invoice_count,
            critical_products=tuple(crit),
        )
        return DashboardResult.success(snapshot)

    except FileNotFoundError:
        return DashboardResult.failure(
            "Αδυναμία φόρτωσης δεδομένων dashboard: "
            f"το αρχείο βάσης δεδομένων δεν βρέθηκε ({db_path})")
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "unable to open" in msg.lower() or "no such" in msg.lower():
            return DashboardResult.failure(
                "Αδυναμία φόρτωσης δεδομένων dashboard: "
                f"αδυναμία ανοίγματος της βάσης δεδομένων ({db_path})")
        return DashboardResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων dashboard: σφάλμα SQLite — {msg}")
    except sqlite3.DatabaseError as e:
        return DashboardResult.failure(
            "Αδυναμία φόρτωσης δεδομένων dashboard: "
            f"σφάλμα βάσης δεδομένων — {e}")
    except Exception as e:
        return DashboardResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων dashboard: {e}")
    finally:
        if conn:
            conn.close()
