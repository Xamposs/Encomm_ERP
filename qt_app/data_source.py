"""Read-only SQLite data source for the Qt application shell.

Every function opens a fresh ``sqlite3`` connection in read-only mode
(``mode=ro`` via URI), runs its queries, and closes the connection.

NO writes, migrations, or schema changes — safe to use alongside the
running CustomTkinter application (which uses a separate connection
pool via ``DatabaseService``).
"""

import sqlite3
from typing import Dict, List, Tuple


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Open a read-only connection.

    Converts a plain path like ``encomm_erp.db`` into a URI with
    ``mode=ro`` so that any write attempt raises ``sqlite3.OperationalError``.
    """
    # Normalise backslashes for URI compatibility on Windows.
    path = db_path.replace("\\", "/")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── Dashboard queries ──────────────────────────────────────────────────

def fetch_dashboard_counts(
    db_path: str, threshold: int = 10, alert_days: int = 30
) -> Dict[str, int]:
    """Return {total, low_stock, expiry} counts."""
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM ProductMaster")
        total = cur.fetchone()["total"]
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM ProductMaster WHERE Stock <= ?",
            (threshold,),
        )
        low_stock = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM ProductMaster "
            "WHERE date(ExpiryDate) < date('now') "
            "   OR date(ExpiryDate) <= date('now', '+' || ? || ' days')",
            (alert_days,),
        )
        expiry = cur.fetchone()["cnt"]
        return {"total": total, "low_stock": low_stock, "expiry": expiry}
    except sqlite3.Error:
        return {"total": 0, "low_stock": 0, "expiry": 0}
    finally:
        if conn:
            conn.close()


def fetch_critical_products(
    db_path: str, threshold: int = 10, alert_days: int = 30, limit: int = 20
) -> List[Tuple[str, int, str, str]]:
    """Return [(name, stock, expiry_date, reason), ...] for critical products.

    Sorted by severity: expired → near-expiry → low-stock.
    """
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT Name, Stock, ExpiryDate,
                   CASE
                       WHEN date(ExpiryDate) < date('now')
                           THEN 1  -- expired (most severe)
                       WHEN date(ExpiryDate) <= date('now', '+' || ? || ' days')
                           THEN 2  -- near expiry
                       WHEN Stock <= ?
                           THEN 3  -- low stock
                   END AS severity
            FROM ProductMaster
            WHERE Stock <= ?
               OR date(ExpiryDate) < date('now')
               OR date(ExpiryDate) <= date('now', '+' || ? || ' days')
            ORDER BY severity ASC, ExpiryDate ASC
            LIMIT ?
            """,
            (alert_days, threshold, threshold, alert_days, limit),
        )
        results = []
        for row in cur.fetchall():
            name = row["Name"]
            stock = row["Stock"]
            expiry = row["ExpiryDate"]
            sev = row["severity"]
            if sev == 1:
                reason = "Ληγμένο" if expiry else "Ληγμένο (άγνωστη ημ/νία)"
            elif sev == 2:
                reason = f"Λήγει σύντομα ({expiry})"
            else:
                reason = "Χαμηλό απόθεμα"
            results.append((name, stock, expiry or "—", reason))
        return results
    except sqlite3.Error:
        return []
    finally:
        if conn:
            conn.close()


def fetch_dashboard_analytics(db_path: str) -> Dict:
    """Return {revenue_today, vat_today, total_revenue, invoice_count}."""
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        revenue_today = cur.execute(
            "SELECT COALESCE(SUM(grand_total), 0) FROM invoices "
            "WHERE date(invoice_date) = date('now')"
        ).fetchone()[0]
        vat_today = cur.execute(
            "SELECT COALESCE(SUM(vat_amount), 0) FROM invoices "
            "WHERE date(invoice_date) = date('now')"
        ).fetchone()[0]
        total_revenue = cur.execute(
            "SELECT COALESCE(SUM(grand_total), 0) FROM invoices"
        ).fetchone()[0]
        invoice_count = cur.execute(
            "SELECT COUNT(*) FROM invoices"
        ).fetchone()[0]
        return {
            "revenue_today": revenue_today,
            "vat_today": vat_today,
            "total_revenue": total_revenue,
            "invoice_count": invoice_count,
        }
    except sqlite3.Error:
        return {"revenue_today": 0, "vat_today": 0,
                "total_revenue": 0, "invoice_count": 0}
    finally:
        if conn:
            conn.close()
