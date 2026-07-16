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


# ═══════════════════════════════════════════════════════════════════════
# Supplier data source (read-only)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SupplierSummary:
    id: int
    name: str
    tax_id: str
    contact_person: str
    phone: str
    email: str
    product_count: int


@dataclass(frozen=True)
class SupplierDetail:
    id: int
    name: str
    phone: str
    email: str
    address: str
    tax_id: str
    contact_person: str
    allowed_sender_emails: str
    catalogue_format: str
    default_markup: str
    pricing_notes: str
    created_at: str
    product_count: int


@dataclass(frozen=True)
class SupplierPageResult:
    ok: bool
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: Tuple[SupplierSummary, ...] = ()
    error_message: str = ""

    @classmethod
    def success(cls, total, page, page_size, items):
        return cls(ok=True, total=total, page=page, page_size=page_size, items=items)

    @classmethod
    def failure(cls, msg):
        return cls(ok=False, error_message=msg)


@dataclass(frozen=True)
class SupplierDetailResult:
    ok: bool
    supplier: SupplierDetail | None = None
    error_message: str = ""

    @classmethod
    def success(cls, sup):
        return cls(ok=True, supplier=sup)

    @classmethod
    def failure(cls, msg):
        return cls(ok=False, error_message=msg)


def _optional_col(cur, table, column, fallback):
    rows = cur.execute(f"PRAGMA table_info('{table}')").fetchall()
    return column if any(r[1] == column for r in rows) else fallback


def load_suppliers_page(db_path, search_text="", page=1, page_size=50):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    conn = None
    try:
        conn = _connect_ro(db_path)
        conn.create_function("search_normalize", 1, _normalize_search, deterministic=True)
        cur = conn.cursor()
        if not _has_table(cur, "suppliers"):
            return SupplierPageResult.failure("Ο πίνακας προμηθευτών δεν υπάρχει.")

        # Schema detection
        has_pm = _has_table(cur, "ProductMaster")
        has_sid = has_pm and _optional_col(cur, "ProductMaster", "supplier_id", None) != "'' AS supplier_id"
        # ^ _optional_col returns the column name when it exists, else the SQL fallback.
        # Better: use _has_column-style approach.
        has_sid = has_pm and _has_column(cur, "ProductMaster", "supplier_id")
        pc_expr = (
            "(SELECT COUNT(*) FROM ProductMaster p WHERE p.supplier_id=s.id) AS pc"
            if has_sid else "0 AS pc")

        email_col = _optional_col(cur, "suppliers", "email", "''")
        has_email = email_col != "''"

        conditions, params = [], []
        if search_text:
            norm = _normalize_search(search_text)
            esc = _escape_like(norm)
            tax_col = _optional_col(cur, "suppliers", "tax_id", "''")
            clauses = [f"search_normalize(s.name) LIKE ? ESCAPE '\\'"]
            params.append(f"%{esc}%")
            if has_email:
                clauses.append(
                    f"search_normalize(COALESCE(s.email,'')) LIKE ? ESCAPE '\\'")
                params.append(f"%{esc}%")
            clauses.append(
                f"search_normalize(COALESCE({tax_col},'')) LIKE ? ESCAPE '\\'")
            params.append(f"%{esc}%")
            conditions.append("(" + " OR ".join(clauses) + ")")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = cur.execute(f"SELECT COUNT(*) FROM suppliers s{where}", params).fetchone()[0]
        if total == 0:
            page = 1
        else:
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size
        tax = _optional_col(cur, "suppliers", "tax_id", "'' AS tax_id")
        cp = _optional_col(cur, "suppliers", "contact_person", "'' AS contact_person")
        ph = _optional_col(cur, "suppliers", "phone", "'' AS phone")
        em = _optional_col(cur, "suppliers", "email", "'' AS email")
        cur.execute(f"""
            SELECT s.id, s.name, {tax}, {cp}, {ph}, {em},
                   {pc_expr}
            FROM suppliers s{where} ORDER BY s.name ASC LIMIT ? OFFSET ?
        """, params + [page_size, offset])
        items = tuple(SupplierSummary(
            id=r[0], name=r[1], tax_id=r[2] or "—", contact_person=r[3] or "—",
            phone=r[4] or "—", email=r[5] or "—", product_count=r[6] or 0)
            for r in cur.fetchall())
        return SupplierPageResult.success(total, page, page_size, items)
    except (sqlite3.Error, FileNotFoundError) as e:
        return SupplierPageResult.failure(f"Αδυναμία φόρτωσης προμηθευτών: {e}")
    except Exception as e:
        return SupplierPageResult.failure(f"Αδυναμία φόρτωσης προμηθευτών: {e}")
    finally:
        if conn:
            conn.close()


def load_supplier_detail(db_path, supplier_id):
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        if not _has_table(cur, "suppliers"):
            return SupplierDetailResult.failure("Ο πίνακας προμηθευτών δεν υπάρχει.")
        def _c(c, fb):
            return _optional_col(cur, "suppliers", c, fb)

        has_pm = _has_table(cur, "ProductMaster")
        has_sid = has_pm and _has_column(cur, "ProductMaster", "supplier_id")
        pc_expr = (
            "(SELECT COUNT(*) FROM ProductMaster p WHERE p.supplier_id=s.id) AS pc"
            if has_sid else "0 AS pc")

        cur.execute(f"""
            SELECT s.id, s.name, {_c('phone',"''")}, {_c('email',"''")},
                   {_c('address',"''")}, {_c('tax_id',"''")},
                   {_c('contact_person',"''")}, {_c('allowed_sender_emails',"''")},
                   {_c('catalogue_format',"''")}, {_c('default_markup',"''")},
                   {_c('pricing_notes',"''")}, {_c('created_at',"''")},
                   {pc_expr}
            FROM suppliers s WHERE s.id=?
        """, (supplier_id,))
        r = cur.fetchone()
        if not r:
            return SupplierDetailResult.failure(f"Ο προμηθευτής {supplier_id} δεν βρέθηκε.")
        return SupplierDetailResult.success(SupplierDetail(
            id=r[0], name=r[1], phone=r[2] or "—", email=r[3] or "—",
            address=r[4] or "—", tax_id=r[5] or "—", contact_person=r[6] or "—",
            allowed_sender_emails=r[7] or "—", catalogue_format=r[8] or "—",
            default_markup=(
                str(int(r[9])) if isinstance(r[9], float) and r[9] == int(r[9])
                else str(r[9])) if r[9] is not None else "—",
            pricing_notes=r[10] or "—", created_at=r[11] or "—", product_count=r[12] or 0))
    except (sqlite3.Error, FileNotFoundError) as e:
        return SupplierDetailResult.failure(f"Αδυναμία φόρτωσης: {e}")
    except Exception as e:
        return SupplierDetailResult.failure(f"Αδυναμία φόρτωσης: {e}")
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Customer data source (read-only)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CustomerSummary:
    id: int
    name: str
    amka: str
    phone: str
    invoice_count: int
    total_purchases: float


@dataclass(frozen=True)
class CustomerDetail:
    id: int
    name: str
    amka: str
    phone: str
    invoice_count: int
    total_purchases: float
    latest_invoice_date: str


@dataclass(frozen=True)
class CustomerPageResult:
    ok: bool
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: Tuple[CustomerSummary, ...] = ()
    error_message: str = ""

    @classmethod
    def success(cls, total, page, page_size, items):
        return cls(ok=True, total=total, page=page, page_size=page_size, items=items)

    @classmethod
    def failure(cls, msg):
        return cls(ok=False, error_message=msg)


@dataclass(frozen=True)
class CustomerDetailResult:
    ok: bool
    customer: CustomerDetail | None = None
    error_message: str = ""

    @classmethod
    def success(cls, cus):
        return cls(ok=True, customer=cus)

    @classmethod
    def failure(cls, msg):
        return cls(ok=False, error_message=msg)


def load_customers_page(db_path, search_text="", page=1, page_size=50):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    conn = None
    try:
        conn = _connect_ro(db_path)
        conn.create_function("search_normalize", 1, _normalize_search, deterministic=True)
        cur = conn.cursor()
        if not _has_table(cur, "customers"):
            return CustomerPageResult.failure("Ο πίνακας πελατών δεν υπάρχει.")

        has_inv = _has_table(cur, "invoices")
        has_cid = has_inv and _has_column(cur, "invoices", "customer_id")
        has_amka = _has_column(cur, "customers", "amka")
        has_phone = _has_column(cur, "customers", "phone")
        amka_search = "c.amka" if has_amka else "''"
        phone_search = "c.phone" if has_phone else "''"
        amka_sel = "c.amka" if has_amka else "'' AS amka"
        phone_sel = "c.phone" if has_phone else "'' AS phone"

        conditions, params = [], []
        if search_text:
            norm = _normalize_search(search_text)
            esc = _escape_like(norm)
            clauses = [f"search_normalize(c.name) LIKE ? ESCAPE '\\'"]
            params.append(f"%{esc}%")
            clauses.append(f"search_normalize(COALESCE({amka_search},'')) LIKE ? ESCAPE '\\'")
            params.append(f"%{esc}%")
            if has_phone:
                clauses.append(f"search_normalize(COALESCE({phone_search},'')) LIKE ? ESCAPE '\\'")
                params.append(f"%{esc}%")
            conditions.append("(" + " OR ".join(clauses) + ")")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        inv_cnt = "(SELECT COUNT(*) FROM invoices i WHERE i.customer_id=c.id)" if has_cid else "0"
        inv_sum = ("(SELECT COALESCE(SUM(grand_total),0) FROM invoices i WHERE i.customer_id=c.id)" if has_cid else "0")

        total = cur.execute(f"SELECT COUNT(*) FROM customers c{where}", params).fetchone()[0]
        if total == 0:
            page = 1
        else:
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        cur.execute(f"""
            SELECT c.id, c.name, {amka_sel}, {phone_sel},
                   {inv_cnt} AS ic, {inv_sum} AS ts
            FROM customers c{where} ORDER BY c.name ASC LIMIT ? OFFSET ?
        """, params + [page_size, offset])
        items = tuple(CustomerSummary(
            id=r[0], name=r[1], amka=r[2] or "—", phone=r[3] or "—",
            invoice_count=r[4] or 0, total_purchases=r[5] or 0.0)
            for r in cur.fetchall())
        return CustomerPageResult.success(total, page, page_size, items)
    except (sqlite3.Error, FileNotFoundError) as e:
        return CustomerPageResult.failure(f"Αδυναμία φόρτωσης πελατών: {e}")
    except Exception as e:
        return CustomerPageResult.failure(f"Αδυναμία φόρτωσης πελατών: {e}")
    finally:
        if conn:
            conn.close()


def load_customer_detail(db_path, customer_id):
    conn = None
    try:
        conn = _connect_ro(db_path)
        cur = conn.cursor()
        if not _has_table(cur, "customers"):
            return CustomerDetailResult.failure("Ο πίνακας πελατών δεν υπάρχει.")

        has_inv = _has_table(cur, "invoices")
        has_cid = has_inv and _has_column(cur, "invoices", "customer_id")
        has_amka = _has_column(cur, "customers", "amka")
        has_phone = _has_column(cur, "customers", "phone")
        amka_sel = "c.amka" if has_amka else "'' AS amka"
        phone_sel = "c.phone" if has_phone else "'' AS phone"
        inv_cnt = ("(SELECT COUNT(*) FROM invoices i WHERE i.customer_id=c.id)" if has_cid else "0")
        inv_sum = ("(SELECT COALESCE(SUM(grand_total),0) FROM invoices i WHERE i.customer_id=c.id)" if has_cid else "0")
        latest_date = ("(SELECT MAX(invoice_date) FROM invoices i WHERE i.customer_id=c.id)" if has_cid else "''")

        cur.execute(f"""
            SELECT c.id, c.name, {amka_sel}, {phone_sel},
                   {inv_cnt}, {inv_sum}, {latest_date}
            FROM customers c WHERE c.id=?
        """, (customer_id,))
        r = cur.fetchone()
        if not r:
            return CustomerDetailResult.failure(f"Ο πελάτης {customer_id} δεν βρέθηκε.")
        return CustomerDetailResult.success(CustomerDetail(
            id=r[0], name=r[1], amka=r[2] or "—", phone=r[3] or "—",
            invoice_count=r[4] or 0, total_purchases=r[5] or 0.0,
            latest_invoice_date=r[6] or "—"))
    except (sqlite3.Error, FileNotFoundError) as e:
        return CustomerDetailResult.failure(f"Αδυναμία φόρτωσης: {e}")
    except Exception as e:
        return CustomerDetailResult.failure(f"Αδυναμία φόρτωσης: {e}")
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Stock Movements data source (read-only)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class StockMovement:
    id: int
    timestamp: str
    barcode: str
    product_name: str
    old_stock: int
    change_amount: int
    new_stock: int
    reason: str
    source: str
    operator: str


@dataclass(frozen=True)
class StockMovementsResult:
    ok: bool
    total: int = 0
    page: int = 1
    page_size: int = 50
    items: Tuple[StockMovement, ...] = ()
    reasons: Tuple[str, ...] = ()
    error_message: str = ""

    @classmethod
    def success(cls, total, page, page_size, items, reasons):
        return cls(ok=True, total=total, page=page, page_size=page_size,
                   items=items, reasons=reasons)

    @classmethod
    def failure(cls, msg):
        return cls(ok=False, error_message=msg)


def load_stock_movements(
    db_path,
    search_text="",
    reason_filter="",
    date_from="",
    date_to="",
    page=1,
    page_size=50,
):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)

    # ── Date validation ──
    from datetime import date as dt_date
    for label, val in [("date_from", date_from), ("date_to", date_to)]:
        if val:
            try:
                dt_date.fromisoformat(val)
            except ValueError:
                return StockMovementsResult.failure(
                    f"Μη έγκυρη ημερομηνία για {label}: '{val}'. "
                    f"Απαιτείται μορφή YYYY-MM-DD.")
    if date_from and date_to:
        try:
            if dt_date.fromisoformat(date_from) > dt_date.fromisoformat(date_to):
                return StockMovementsResult.failure(
                    "Η ημερομηνία 'από' δεν μπορεί να είναι μετά την ημερομηνία 'έως'.")
        except ValueError:
            pass  # already caught above

    conn = None
    try:
        conn = _connect_ro(db_path)
        conn.create_function("search_normalize", 1, _normalize_search, deterministic=True)
        cur = conn.cursor()
        if not _has_table(cur, "stock_movements"):
            return StockMovementsResult.failure(
                "Ο πίνακας κινήσεων αποθήκης δεν υπάρχει.")

        has_ca = _has_column(cur, "stock_movements", "change_amount")
        has_src = _has_column(cur, "stock_movements", "source")
        has_op = _has_column(cur, "stock_movements", "operator")
        has_diff = _has_column(cur, "stock_movements", "difference")
        has_rid = _has_column(cur, "stock_movements", "reference_id")

        if not has_ca and not has_diff:
            return StockMovementsResult.failure(
                "Δεν βρέθηκε στήλη μεταβολής (change_amount ή difference).")

        # Required columns
        required = ["timestamp", "barcode", "product_name",
                    "old_stock", "new_stock", "reason"]
        for col in required:
            if not _has_column(cur, "stock_movements", col):
                conn.close()
                return StockMovementsResult.failure(
                    f"Λείπει η υποχρεωτική στήλη '{col}' στον πίνακα stock_movements.")

        change_expr = (
            "COALESCE(change_amount, difference)" if (has_ca and has_diff)
            else "change_amount" if has_ca else "difference")
        source_expr = "COALESCE(source, reference_id)" if (has_src and has_rid) else (
            "source" if has_src else ("reference_id" if has_rid else "''"))
        op_expr = "operator" if has_op else "''"

        conditions, params = [], []
        if search_text:
            norm = _normalize_search(search_text)
            esc = _escape_like(norm)
            conditions.append(
                "(search_normalize(sm.product_name) LIKE ? ESCAPE '\\' "
                "OR search_normalize(sm.barcode) LIKE ? ESCAPE '\\')")
            params.extend([f"%{esc}%", f"%{esc}%"])
        if reason_filter:
            conditions.append("sm.reason = ?")
            params.append(reason_filter)
        if date_from:
            conditions.append("sm.timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("datetime(sm.timestamp) < datetime(?, '+1 day')")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total = cur.execute(
            f"SELECT COUNT(*) FROM stock_movements sm{where}", params).fetchone()[0]
        if total == 0:
            page = 1
        else:
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        cur.execute(f"""
            SELECT sm.id, sm.timestamp, sm.barcode, sm.product_name,
                   sm.old_stock, {change_expr}, sm.new_stock,
                   sm.reason, {source_expr}, {op_expr}
            FROM stock_movements sm{where}
            ORDER BY sm.timestamp DESC, sm.id DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset])
        items = tuple(StockMovement(
            id=r[0], timestamp=r[1], barcode=r[2], product_name=r[3],
            old_stock=r[4], change_amount=r[5], new_stock=r[6],
            reason=r[7], source=r[8] or "—", operator=r[9] or "—")
            for r in cur.fetchall())

        reasons = tuple(
            r[0] for r in cur.execute(
                "SELECT DISTINCT reason FROM stock_movements ORDER BY reason"))
        return StockMovementsResult.success(total, page, page_size, items, reasons)

    except (sqlite3.Error, FileNotFoundError) as e:
        return StockMovementsResult.failure(
            f"Αδυναμία φόρτωσης κινήσεων: {e}")
    except Exception as e:
        return StockMovementsResult.failure(
            f"Αδυναμία φόρτωσης κινήσεων: {e}")
    finally:
        if conn:
            conn.close()
