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
import unicodedata
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
    expiry_date: str,
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
# ═══════════════════════════════════════════════════════════════════════
# Inventory data source
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class InventoryProduct:
    """Single inventory row (immutable snapshot)."""
    barcode: str
    name: str
    stock: int
    expiry_date: str
    price: float
    supplier_id: int | None
    supplier_name: str
    status_labels: Tuple[str, ...]


@dataclass(frozen=True)
class InventorySnapshot:
    """Immutable page of inventory data."""
    total_matching: int
    page: int
    page_size: int
    products: Tuple[InventoryProduct, ...]


@dataclass(frozen=True)
class InventoryResult:
    ok: bool
    snapshot: InventorySnapshot | None = None
    error_message: str = ""

    @classmethod
    def success(cls, snapshot: InventorySnapshot) -> "InventoryResult":
        return cls(ok=True, snapshot=snapshot)

    @classmethod
    def failure(cls, message: str) -> "InventoryResult":
        return cls(ok=False, error_message=message)


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards so user input is treated as literal text."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_search(value: str) -> str:
    """Casefold + strip Greek tonos for accent-insensitive search.

    Normalises to NFD, drops all combining marks (category ``Mn``),
    then recomposes the remaining base characters.  Result is suitable
    for both the SQLite registered function and for normalising user
    input before LIKE escaping.
    """
    s = str(value).casefold()
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(
        ch for ch in decomposed
        if unicodedata.category(ch) != "Mn"
    )
    return unicodedata.normalize("NFC", stripped)


def _has_table(cur, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _has_column(cur, table: str, column: str) -> bool:
    rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    return any(r[1] == column for r in rows)


def load_inventory_page(
    db_path: str,
    search_text: str = "",
    status_filter: str = "all",
    threshold: int = 10,
    alert_days: int = 30,
    page: int = 1,
    page_size: int = 50,
) -> InventoryResult:
    """Return a paginated, filtered, searched inventory view.

    One read-only connection — no writes, no schema changes.
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 100)

    conn = None
    try:
        conn = _connect_ro(db_path)
        # Register accent+case-normalised search function
        conn.create_function("search_normalize", 1, _normalize_search, deterministic=True)
        cur = conn.cursor()

        # ── Schema detection (read-only PRAGMA only) ──
        has_suppliers_table = _has_table(cur, "suppliers")
        has_supplier_id = _has_column(cur, "ProductMaster", "supplier_id")

        # ── Build WHERE clause ──
        conditions: List[str] = []
        params: List = []

        if search_text:
            normalized = _normalize_search(search_text)
            escaped = _escape_like(normalized)
            conditions.append(
                "(search_normalize(p.Name) LIKE ? ESCAPE '\\' "
                "OR search_normalize(p.Barcode) LIKE ? ESCAPE '\\')")
            params.extend([f"%{escaped}%", f"%{escaped}%"])

        if status_filter == "expired":
            conditions.append(
                "p.ExpiryDate != '' AND date(p.ExpiryDate) < date('now')")
        elif status_filter == "near_expiry":
            conditions.append(
                "p.ExpiryDate != '' AND date(p.ExpiryDate) >= date('now') "
                "AND date(p.ExpiryDate) <= date('now', '+' || ? || ' days')")
            params.append(alert_days)
        elif status_filter == "low_stock":
            conditions.append("p.Stock <= ?")
            params.append(threshold)
        elif status_filter == "available":
            conditions.append(
                "p.Stock > ? AND (p.ExpiryDate = '' "
                "OR (date(p.ExpiryDate) >= date('now') "
                "AND date(p.ExpiryDate) > date('now', '+' || ? || ' days')))")
            params.extend([threshold, alert_days])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        # ── Count ──
        count_sql = f"SELECT COUNT(*) FROM ProductMaster p{where}"
        total = cur.execute(count_sql, params).fetchone()[0]

        # Pagination bounds — clamp after knowing total
        if total == 0:
            page = 1
        else:
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        # ── Supplier column — build dynamically ──
        if has_suppliers_table and has_supplier_id:
            supplier_select = "p.supplier_id, COALESCE(s.name, '—') AS supplier_name"
            supplier_join = "LEFT JOIN suppliers s ON p.supplier_id = s.id"
        else:
            supplier_select = (
                "p.supplier_id AS supplier_id" if has_supplier_id
                else "NULL AS supplier_id") + ", '—' AS supplier_name"
            supplier_join = ""

        # ── Products ──
        data_sql = f"""
            SELECT p.Barcode, p.Name, p.Stock, p.ExpiryDate, p.Price,
                   {supplier_select},
                   CASE WHEN p.ExpiryDate != ''
                             AND date(p.ExpiryDate) < date('now')
                        THEN 1 ELSE 0 END AS is_expired,
                   CASE WHEN p.ExpiryDate != ''
                             AND date(p.ExpiryDate) >= date('now')
                             AND date(p.ExpiryDate) <=
                                 date('now', '+' || ? || ' days')
                        THEN 1 ELSE 0 END AS is_near_expiry
            FROM ProductMaster p
            {supplier_join}
            {where}
            ORDER BY p.Name ASC
            LIMIT ? OFFSET ?
        """
        all_params = [alert_days] + params + [page_size, offset]
        cur.execute(data_sql, all_params)
        products: List[InventoryProduct] = []
        for row in cur.fetchall():
            statuses = _build_reasons_from_flags(
                is_expired=row["is_expired"],
                is_near_expiry=row["is_near_expiry"],
                stock=row["Stock"],
                threshold=threshold,
                expiry_date=row["ExpiryDate"] or "—",
            )
            sid = row["supplier_id"]
            products.append(InventoryProduct(
                barcode=row["Barcode"],
                name=row["Name"],
                stock=row["Stock"],
                expiry_date=row["ExpiryDate"] or "—",
                price=row["Price"],
                supplier_id=sid if sid is not None else None,
                supplier_name=row["supplier_name"],
                status_labels=statuses,
            ))

        return InventoryResult.success(InventorySnapshot(
            total_matching=total,
            page=page,
            page_size=page_size,
            products=tuple(products),
        ))

    except FileNotFoundError:
        return InventoryResult.failure(
            "Αδυναμία φόρτωσης δεδομένων αποθήκης: "
            f"το αρχείο βάσης δεν βρέθηκε ({db_path})")
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "unable to open" in msg.lower() or "no such" in msg.lower():
            return InventoryResult.failure(
                "Αδυναμία φόρτωσης δεδομένων αποθήκης: "
                f"αδυναμία ανοίγματος της βάσης ({db_path})")
        return InventoryResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων αποθήκης: σφάλμα SQLite — {msg}")
    except sqlite3.DatabaseError as e:
        return InventoryResult.failure(
            "Αδυναμία φόρτωσης δεδομένων αποθήκης: "
            f"σφάλμα βάσης — {e}")
    except Exception as e:
        return InventoryResult.failure(
            f"Αδυναμία φόρτωσης δεδομένων αποθήκης: {e}")
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Supplier choices (read-only)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SupplierChoice:
    id: int
    name: str


def load_supplier_choices(db_path: str) -> Tuple[SupplierChoice, ...]:
    """Return all suppliers (id, name) sorted alphabetically.

    Graceful fallback to empty tuple when the suppliers table does not
    exist or is unreadable.  Never writes.
    """
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM suppliers ORDER BY name ASC")
        return tuple(SupplierChoice(id=r[0], name=r[1]) for r in cur.fetchall())
    except (sqlite3.Error, FileNotFoundError):
        return ()
    finally:
        if conn:
            conn.close()
