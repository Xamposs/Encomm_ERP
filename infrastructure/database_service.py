import sqlite3
import os
import logging
from typing import List, Optional, Dict, Tuple
from core.domain_models import Product

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "encomm_erp.db")

class DatabaseService:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Establish a connection to the SQLite database with Row factory enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # PRAGMA optimizations for high-performance bulk operations
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        return conn

    def _initialize_db(self):
        """Create the database file and ProductMaster table if they do not exist."""
        conn = None
        try:
            # Ensure the directory exists if db_path is nested
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Execute raw SQL to create the table structure
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ProductMaster (
                    Barcode TEXT PRIMARY KEY,
                    Name TEXT NOT NULL,
                    Stock INTEGER NOT NULL,
                    ExpiryDate TEXT NOT NULL,
                    Price REAL NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS SystemConfig (
                    Key   TEXT PRIMARY KEY,
                    Value TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id           TEXT PRIMARY KEY,
                    invoice_date TEXT    NOT NULL,
                    subtotal     REAL    NOT NULL,
                    vat_amount   REAL    NOT NULL,
                    grand_total  REAL    NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoice_items (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id TEXT    NOT NULL,
                    barcode    TEXT    NOT NULL,
                    name       TEXT    NOT NULL,
                    quantity   INTEGER NOT NULL,
                    price      REAL    NOT NULL,
                    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    name  TEXT    NOT NULL,
                    amka  TEXT    UNIQUE,
                    phone TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS suppliers (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name    TEXT    NOT NULL UNIQUE,
                    phone   TEXT,
                    email   TEXT,
                    address TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_movements (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp    TEXT    NOT NULL,
                    barcode      TEXT    NOT NULL,
                    product_name TEXT    NOT NULL,
                    old_stock    INTEGER NOT NULL,
                    new_stock    INTEGER NOT NULL,
                    difference   INTEGER NOT NULL,
                    reason       TEXT    NOT NULL,
                    reference_id TEXT
                )
            """)

            # Safely add supplier_id column to ProductMaster if it doesn't exist
            try:
                cursor.execute("ALTER TABLE ProductMaster ADD COLUMN supplier_id INTEGER")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # ── Performance Indexes ──
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_expiry ON ProductMaster(ExpiryDate)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_supplier ON ProductMaster(supplier_id)")

            # Add customer_id to invoices if it doesn't exist (migration-safe)
            try:
                cursor.execute("ALTER TABLE invoices ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
            except sqlite3.OperationalError:
                pass  # column already exists

            # ── Stock Movement Audit Trail (migration-safe) ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_movements (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT    NOT NULL,
                    barcode       TEXT    NOT NULL,
                    product_name  TEXT    NOT NULL,
                    old_stock     INTEGER NOT NULL,
                    new_stock     INTEGER NOT NULL,
                    change_amount INTEGER NOT NULL,
                    reason        TEXT    NOT NULL,
                    source        TEXT,
                    operator      TEXT    DEFAULT 'Σύστημα'
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sm_barcode ON stock_movements(barcode)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sm_timestamp ON stock_movements(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sm_reason ON stock_movements(reason)")

            conn.commit()

            # Performance indexes for 100K+ row queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_name ON ProductMaster(Name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_stock ON ProductMaster(Stock)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_expiry ON ProductMaster(ExpiryDate)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inv_items_invoice ON invoice_items(invoice_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_movements_barcode ON stock_movements(barcode)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_movements_timestamp ON stock_movements(timestamp)")
            conn.commit()

            # Check if table is empty, and insert premium mock data for demonstration
            cursor.execute("SELECT COUNT(*) as count FROM ProductMaster")
            row = cursor.fetchone()
            if row and row['count'] == 0:
                self._insert_dummy_data(cursor)
                conn.commit()

            # Verify WAL journal mode is active
            self._verify_wal(conn)
        except sqlite3.Error as e:
            logging.error(f"Database initialization error: {e}")
            raise RuntimeError(f"Failed to initialize database at '{self.db_path}': {e}") from e
        finally:
            if conn:
                conn.close()

    def _insert_dummy_data(self, cursor: sqlite3.Cursor):
        """Seed the database with high-quality sample pharmacy items."""
        dummy_products = [
            ("8801234567890", "Paracetamol 500mg (Panadol)", 150, "2027-08-15", 3.50, None),
            ("8801234567891", "Amoxicillin 250mg (Antibiotic)", 8, "2026-06-10", 12.99, None), # Low stock & Near expiry (rel. to June 2026)
            ("8801234567892", "Ibuprofen 400mg (Advil)", 80, "2027-01-20", 5.25, None),
            ("8801234567893", "Atorvastatin 20mg (Lipitor)", 4, "2026-05-30", 25.00, None), # Low stock & Expired
            ("8801234567894", "Metformin 850mg (Glucophage)", 200, "2028-11-05", 9.80, None),
            ("8801234567895", "Omeprazole 20mg (Prilosec)", 12, "2026-06-22", 8.45, None), # Near expiry
            ("8801234567896", "Lisinopril 10mg (Zestril)", 95, "2027-04-12", 11.20, None),
            ("8801234567897", "Cetirizine 10mg (Zyrtec)", 180, "2028-02-18", 4.99, None),
        ]
        cursor.executemany("""
            INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, dummy_products)

    def get_all_products(self) -> List[Product]:
        """Fetch all product records from ProductMaster, ordered by name."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id FROM ProductMaster ORDER BY Name ASC")
            rows = cursor.fetchall()
            products = [
                Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"],
                    supplier_id=row["supplier_id"]
                )
                for row in rows
            ]
            return products
        except sqlite3.Error as e:
            logging.error(f"Error fetching all products: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_in_stock_products_limited(self, limit: int = 20) -> List[Product]:
        """Return up to `limit` in-stock products for lightweight UI dropdowns."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id "
                "FROM ProductMaster WHERE Stock > 0 ORDER BY Name ASC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            products = [
                Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"],
                    supplier_id=row["supplier_id"],
                )
                for row in rows
            ]
            return products
        except sqlite3.Error as e:
            logging.error(f"Error in get_in_stock_products_limited: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_product(self, barcode: str) -> Optional[Product]:
        """Fetch a single product by barcode."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id FROM ProductMaster WHERE Barcode = ?", (barcode,))
            row = cursor.fetchone()
            product = None
            if row:
                product = Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"],
                    supplier_id=row["supplier_id"]
                )
            return product
        except sqlite3.Error as e:
            logging.error(f"Error fetching product with barcode '{barcode}': {e}")
            return None
        finally:
            if conn:
                conn.close()

    def add_product(self, product: Product) -> bool:
        """Insert a new product record."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            sid = getattr(product, 'supplier_id', None)
            cursor.execute("""
                INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (product.barcode, product.name, product.stock, product.expiry_date, product.price, sid))
            conn.commit()
            # ── Audit trail ──
            try:
                self.log_stock_movement(
                    product.barcode, product.name, 0, product.stock,
                    reason="Εισαγωγή", source="Φόρμα Προϊόντος")
            except Exception:
                pass
            return True
        except sqlite3.Error as e:
            logging.error(f"Error adding product '{product.name}': {e}")
            return False
        finally:
            if conn:
                conn.close()

    def update_product(self, product: Product) -> bool:
        """Update an existing product record."""
        conn = None
        try:
            # Capture old stock for audit before updating
            old = self.get_product(product.barcode)
            old_stock = old.stock if old else 0
            conn = self._get_connection()
            cursor = conn.cursor()
            sid = getattr(product, 'supplier_id', None)
            cursor.execute("""
                UPDATE ProductMaster
                SET Name = ?, Stock = ?, ExpiryDate = ?, Price = ?, supplier_id = ?
                WHERE Barcode = ?
            """, (product.name, product.stock, product.expiry_date, product.price, sid, product.barcode))
            conn.commit()
            # ── Audit trail ──
            try:
                if old_stock != product.stock:
                    self.log_stock_movement(
                        product.barcode, product.name, old_stock, product.stock,
                        reason="Χειροκίνητη Ενημέρωση", source="Φόρμα Προϊόντος")
            except Exception:
                pass
            return True
        except sqlite3.Error as e:
            logging.error(f"Error updating product '{product.barcode}': {e}")
            return False
        finally:
            if conn:
                conn.close()

    def update_stock(self, barcode: str, new_stock: int) -> bool:
        """Update the stock count of an existing product."""
        conn = None
        try:
            # Capture old stock for audit before updating
            old = self.get_product(barcode)
            old_stock = old.stock if old else 0
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE ProductMaster
                SET Stock = ?
                WHERE Barcode = ?
            """, (new_stock, barcode))
            conn.commit()
            # ── Audit trail ──
            try:
                if old_stock != new_stock:
                    self.log_stock_movement(
                        barcode, old.name if old else "", old_stock, new_stock,
                        reason="Χειροκίνητη Ενημέρωση", source="Ενημέρωση Στοκ")
            except Exception:
                pass
            return True
        except sqlite3.Error as e:
            logging.error(f"Error updating stock for barcode '{barcode}': {e}")
            return False
        finally:
            if conn:
                conn.close()

    def delete_product(self, barcode: str) -> bool:
        """Delete a product by barcode."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ProductMaster WHERE Barcode = ?", (barcode,))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error(f"Error deleting product '{barcode}': {e}")
            return False
        finally:
            if conn:
                conn.close()

    # =================================================================
    # BULK UPSERT  (high-speed mass import)
    # =================================================================
    def bulk_upsert_products(self, products_list):
        """Insert or update a large batch of products in one transaction."""
        if not products_list:
            return
        logging.info(f"Bulk upsert started for {len(products_list)} products.")
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                cursor.executemany(
                    """
                    INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(Barcode) DO UPDATE SET
                        Name       = excluded.Name,
                        Stock      = Stock + excluded.Stock,
                        ExpiryDate = excluded.ExpiryDate,
                        Price      = excluded.Price,
                        supplier_id = COALESCE(excluded.supplier_id, ProductMaster.supplier_id)
                    """,
                    products_list,
                )
                conn.commit()
                logging.info(f"Bulk upsert completed for {len(products_list)} items.")
                # ── Audit trail: log each product import ──
                for prod in products_list:
                    try:
                        barcode, name, stock = prod[0], prod[1], prod[2]
                        self.log_stock_movement(
                            barcode, name, 0, stock,
                            reason="Εισαγωγή", source="Τιμολόγιο")
                    except Exception:
                        pass
            except Exception:
                conn.rollback()
                logging.exception(f"Bulk upsert failed for batch of {len(products_list)} items — rolled back.")
                raise
            finally:
                conn.close()
        except Exception:
            logging.exception(f"Bulk upsert connection error for batch of {len(products_list)} items.")
            raise

    # =================================================================
    # OPTIMIZED QUERIES  (native SQL — no Python-side loops)
    # =================================================================
    def get_dashboard_counts(self, threshold: int, alert_days: int) -> Dict[str, int]:
        """Return total, low-stock count, and expiry-alert count via pure SQL COUNT."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total FROM ProductMaster")
            total = cursor.fetchone()["total"]

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM ProductMaster WHERE Stock <= ?",
                (threshold,),
            )
            low_stock = cursor.fetchone()["cnt"]

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM ProductMaster "
                "WHERE date(ExpiryDate) < date('now') "
                "   OR date(ExpiryDate) <= date('now', '+' || ? || ' days')",
                (alert_days,),
            )
            expiry = cursor.fetchone()["cnt"]

            return {"total": total, "low_stock": low_stock, "expiry": expiry}
        except sqlite3.Error as e:
            logging.error(f"Error in get_dashboard_counts: {e}")
            return {"total": 0, "low_stock": 0, "expiry": 0}
        finally:
            if conn:
                conn.close()

    def get_critical_products_sliced(self, threshold: int, alert_days: int, limit: int = 100) -> List[Tuple[Product, str]]:
        """Return up to `limit` most critical products sorted by severity (expired -> near-expiry -> low-stock)."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id,
                       CASE
                           WHEN date(ExpiryDate) < date('now')                          THEN 0
                           WHEN date(ExpiryDate) <= date('now', '+' || ? || ' days')    THEN 1
                           WHEN Stock <= ?                                                THEN 2
                       END AS severity,
                       CASE
                           WHEN date(ExpiryDate) < date('now')                          THEN 1
                           WHEN date(ExpiryDate) <= date('now', '+' || ? || ' days')    THEN 1
                           ELSE 0
                       END AS is_expiry_flag
                FROM ProductMaster
                WHERE Stock <= ?
                   OR date(ExpiryDate) < date('now')
                   OR date(ExpiryDate) <= date('now', '+' || ? || ' days')
                ORDER BY severity ASC, ExpiryDate ASC
                LIMIT ?
                """,
                (alert_days, threshold, alert_days, threshold, alert_days, limit),
            )
            rows = cursor.fetchall()

            results: List[Tuple[Product, str]] = []
            for row in rows:
                p = Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"],
                    supplier_id=row["supplier_id"],
                )
                reasons = []
                if row["is_expiry_flag"]:
                    if row["severity"] == 0:
                        reasons.append("Ληγμένο 🔴")
                    else:
                        reasons.append("Λήγει Σύντομα 🟡")
                if row["Stock"] <= threshold:
                    reasons.append("Χαμηλό Στοκ")
                results.append((p, ", ".join(reasons) if reasons else "Χαμηλό Στοκ"))
            return results
        except sqlite3.Error as e:
            logging.error(f"Error in get_critical_products_sliced: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_products_paginated(
        self,
        search_query: str = "",
        filter_low_stock: bool = False,
        filter_expiry: bool = False,
        threshold: int = 10,
        alert_days: int = 30,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Product], int]:
        """Return one page of filtered products + total matching count via native SQL."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            conditions = []
            params: list = []

            if search_query:
                conditions.append("(LOWER(Name) LIKE ? OR LOWER(Barcode) LIKE ?)")
                like = f"%{search_query.lower()}%"
                params.extend([like, like])

            if filter_low_stock:
                conditions.append("Stock <= ?")
                params.append(threshold)

            if filter_expiry:
                conditions.append(
                    "(date(ExpiryDate) < date('now') "
                    " OR date(ExpiryDate) <= date('now', '+' || ? || ' days'))"
                )
                params.append(alert_days)

            where_clause = (" AND " .join(conditions)) if conditions else "1=1"

            # Total count
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM ProductMaster WHERE {where_clause}", params)
            total_count = cursor.fetchone()["cnt"]

            # Page slice
            cursor.execute(
                f"SELECT Barcode, Name, Stock, ExpiryDate, Price, supplier_id "
                f"FROM ProductMaster WHERE {where_clause} "
                f"ORDER BY Name ASC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            rows = cursor.fetchall()

            # Hard safety: never return more than `limit` rows
            if len(rows) > limit:
                rows = rows[:limit]

            products = [
                Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"],
                    supplier_id=row["supplier_id"],
                )
                for row in rows
            ]
            return products, total_count
        except sqlite3.Error as e:
            logging.error(f"Error in get_products_paginated: {e}")
            return [], 0
        finally:
            if conn:
                conn.close()

    # =================================================================
    # SYSTEM CONFIG  (persistent key-value store)
    # =================================================================
    def get_config(self, key: str, default: str = None) -> Optional[str]:
        """Retrieve a single config value by key."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT Value FROM SystemConfig WHERE Key = ?", (key,))
            row = cursor.fetchone()
            return row["Value"] if row else default
        except sqlite3.Error as e:
            logging.error(f"Error reading config '{key}': {e}")
            return default
        finally:
            if conn:
                conn.close()

    def set_config(self, key: str, value: str):
        """Upsert a single config key-value pair."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO SystemConfig (Key, Value) VALUES (?, ?) "
                "ON CONFLICT(Key) DO UPDATE SET Value = excluded.Value",
                (key, str(value)),
            )
            conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Error writing config '{key}': {e}")
        finally:
            if conn:
                conn.close()

    def get_all_config(self) -> Dict[str, str]:
        """Return all config entries as a dictionary."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT Key, Value FROM SystemConfig")
            rows = cursor.fetchall()
            return {row["Key"]: row["Value"] for row in rows}
        except sqlite3.Error as e:
            logging.error(f"Error loading all config: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    # =================================================================
    # TYPED CONFIG ACCESS & FIRST-RUN SEEDING
    # =================================================================
    def get_config_typed(self, key: str, default, type_fn):
        """Read a config string from DB and convert it via type_fn.
        Returns default if key is missing or conversion fails.
        Logs a warning on conversion failure.
        """
        raw = self.get_config(key)
        if raw is None:
            return default
        try:
            return type_fn(raw)
        except (ValueError, TypeError) as e:
            logging.warning(
                f"Config '{key}' value '{raw}' could not be converted "
                f"using {type_fn.__name__}: {e}"
            )
            return default

    def seed_default_config(self, defaults: dict):
        """Insert default config values for keys that do not already exist.
        Uses INSERT OR IGNORE in a single transaction for atomicity.
        This prevents overwriting user-saved settings on restart.
        """
        if not defaults:
            return
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                cursor.executemany(
                    "INSERT OR IGNORE INTO SystemConfig (Key, Value) VALUES (?, ?)",
                    list(defaults.items()),
                )
                conn.commit()
                logging.info(
                    f"Seeded {len(defaults)} default config keys "
                    f"(existing keys preserved)."
                )
            except Exception:
                conn.rollback()
                logging.exception("Failed to seed default config — rolled back.")
                raise
        except sqlite3.Error as e:
            logging.error(f"Error seeding default config: {e}")
        finally:
            if conn:
                conn.close()

    def config_exists(self, key: str) -> bool:
        """Return True if the given config key exists in the database."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM SystemConfig WHERE Key = ?", (key,))
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logging.error(f"Error checking config existence for '{key}': {e}")
            return False
        finally:
            if conn:
                conn.close()

    def bulk_set_config(self, items: dict):
        """Atomically upsert multiple config key-value pairs
        in a single transaction using executemany."""
        if not items:
            return
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                cursor.executemany(
                    "INSERT INTO SystemConfig (Key, Value) VALUES (?, ?) "
                    "ON CONFLICT(Key) DO UPDATE SET Value = excluded.Value",
                    list(items.items()),
                )
                conn.commit()
                logging.info(f"Bulk set {len(items)} config keys.")
            except Exception:
                conn.rollback()
                logging.exception("Bulk set config failed — rolled back.")
                raise
        except sqlite3.Error as e:
            logging.error(f"Error in bulk_set_config: {e}")
        finally:
            if conn:
                conn.close()

    def _verify_wal(self, conn: sqlite3.Connection):
        """Check that WAL journal mode is active and log confirmation."""
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode")
            row = cursor.fetchone()
            mode = row[0] if row else "unknown"
            if mode.upper() == "WAL":
                logging.info("WAL journal mode is active — confirmed.")
            else:
                logging.warning(f"Expected WAL journal mode but got '{mode}'.")
        except sqlite3.Error as e:
            logging.warning(f"Could not verify WAL journal mode: {e}")

    # =================================================================
    # INVOICE TRANSACTION LOGGING
    # =================================================================
    def save_invoice_transaction(
        self,
        invoice_id: str,
        subtotal: float,
        vat_amount: float,
        grand_total: float,
        items_list,
        customer_id: int = None,
    ) -> bool:
        """Atomically persist an invoice master row and its line items.

        ``items_list`` is a list of ``(Product, quantity)`` tuples matching
        the cart signature used throughout the POS layer.
        ``customer_id`` optionally links the invoice to a customers row.
        """
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "INSERT INTO invoices (id, invoice_date, subtotal, "
                    "vat_amount, grand_total, customer_id) "
                    "VALUES (?, datetime('now'), ?, ?, ?, ?)",
                    (invoice_id, subtotal, vat_amount, grand_total, customer_id),
                )
                conn.executemany(
                    "INSERT INTO invoice_items "
                    "(invoice_id, barcode, name, quantity, price) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (invoice_id, p.barcode, p.name, qty, p.price)
                        for p, qty in items_list
                    ],
                )
                conn.commit()
                logging.info(
                    "Invoice %s saved with %d items.",
                    invoice_id, len(items_list),
                )
                return True
            except Exception:
                conn.rollback()
                logging.exception(
                    "Failed to save invoice %s — rolled back.", invoice_id
                )
                return False
        except sqlite3.Error as e:
            logging.error("DB error saving invoice %s: %s", invoice_id, e)
            return False
        finally:
            if conn:
                conn.close()

    # =================================================================
    # CUSTOMER REGISTRY
    # =================================================================
    def add_customer(self, name: str, amka: str = "", phone: str = "") -> bool:
        """Insert a new customer; AMKA is optional but unique when provided."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO customers (name, amka, phone) VALUES (?, ?, ?)",
                (name, amka or None, phone or None),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            logging.warning("Customer with AMKA '%s' already exists.", amka)
            return False
        except sqlite3.Error as e:
            logging.error("Error adding customer: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    # ── Supplier CRUD ──
    def get_all_suppliers(self) -> List[Dict]:
        """Return all suppliers as a list of dicts."""
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute("SELECT id, name, phone, email, address FROM suppliers ORDER BY name").fetchall()
            return [{"id": r["id"], "name": r["name"], "phone": r["phone"] or "",
                     "email": r["email"] or "", "address": r["address"] or ""} for r in rows]
        except sqlite3.Error as e:
            logging.error("Error fetching suppliers: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def add_supplier(self, name: str, phone: str = "", email: str = "", address: str = "") -> bool:
        """Add a new supplier. Returns True on success."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("INSERT INTO suppliers (name, phone, email, address) VALUES (?, ?, ?, ?)",
                         (name, phone, email, address))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error adding supplier '%s': %s", name, e)
            return False
        finally:
            if conn:
                conn.close()

    def delete_supplier(self, supplier_id: int) -> bool:
        """Delete a supplier by ID. Returns True on success."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error deleting supplier %s: %s", supplier_id, e)
            return False
        finally:
            if conn:
                conn.close()

    def get_low_stock_by_supplier(self) -> Dict[int, List[Dict]]:
        """Return low-stock products grouped by supplier_id. Reads threshold from SystemConfig."""
        conn = None
        try:
            threshold = self.get_config_typed("low_stock_threshold", 10, int)
            conn = self._get_connection()
            rows = conn.execute("""
                SELECT p.Barcode, p.Name, p.Stock, p.supplier_id, s.name as supplier_name
                FROM ProductMaster p
                LEFT JOIN suppliers s ON p.supplier_id = s.id
                WHERE p.Stock <= ? AND p.supplier_id IS NOT NULL
                ORDER BY s.name, p.Name
            """, (threshold,)).fetchall()
            result = {}
            for r in rows:
                sid = r["supplier_id"]
                if sid not in result:
                    result[sid] = []
                result[sid].append({
                    "barcode": r["Barcode"],
                    "name": r["Name"],
                    "stock": r["Stock"],
                    "supplier_id": sid,
                    "supplier_name": r["supplier_name"] or "\u0386\u03b3\u03bd\u03c9\u03c3\u03c4\u03bf\u03c2",
                })
            return result
        except sqlite3.Error as e:
            logging.error("Error fetching low-stock by supplier: %s", e)
            return {}
        finally:
            if conn:
                conn.close()

    def get_all_customers(self) -> List[Dict]:
        """Return all customers ordered by name."""
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT id, name, amka, phone FROM customers ORDER BY name ASC"
            ).fetchall()
            return [{"id": r["id"], "name": r["name"], "amka": r["amka"] or "", "phone": r["phone"] or ""} for r in rows]
        except sqlite3.Error as e:
            logging.error("Error fetching customers: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def search_customers(self, query: str) -> List[Dict]:
        """Search customers by name, AMKA, or phone."""
        conn = None
        try:
            conn = self._get_connection()
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT id, name, amka, phone FROM customers "
                "WHERE name LIKE ? OR amka LIKE ? OR phone LIKE ? "
                "ORDER BY name ASC LIMIT 50",
                (like, like, like),
            ).fetchall()
            return [{"id": r["id"], "name": r["name"], "amka": r["amka"] or "", "phone": r["phone"] or ""} for r in rows]
        except sqlite3.Error as e:
            logging.error("Error searching customers: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def delete_customer(self, customer_id: int) -> bool:
        """Delete a customer by ID. Returns True on success, False on failure."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error deleting customer %s: %s", customer_id, e)
            return False
        finally:
            if conn:
                conn.close()

    # =================================================================
    # INVOICE HISTORY
    # =================================================================
    def get_customer_purchase_history(self, customer_id: int, limit: int = 5) -> List[Dict]:
        """Return the last N purchased items for a given customer via JOIN."""
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute("""
                SELECT ii.name, ii.quantity, ii.price, i.invoice_date
                FROM invoice_items ii
                JOIN invoices i ON ii.invoice_id = i.id
                WHERE i.customer_id = ?
                ORDER BY i.invoice_date DESC
                LIMIT ?
            """, (customer_id, limit)).fetchall()
            return [{
                "name": r["name"], "qty": r["quantity"],
                "price": r["price"], "date": r["invoice_date"]
            } for r in rows]
        except sqlite3.Error as e:
            logging.error("Error fetching purchase history for customer %s: %s", customer_id, e)
            return []
        finally:
            if conn:
                conn.close()

    def get_all_invoices(
        self,
        search_id: str = "",
        start_date: str = None,
        end_date: str = None,
        customer_id: int = None,
    ) -> List[Dict]:
        """Return invoices optionally filtered by ID, date range, or customer."""
        conn = None
        try:
            conn = self._get_connection()
            conditions = []
            params = []
            if search_id:
                conditions.append("i.id LIKE ?")
                params.append(f"%{search_id}%")
            if start_date:
                conditions.append("date(i.invoice_date) >= date(?)")
                params.append(start_date)
            if end_date:
                conditions.append("date(i.invoice_date) <= date(?)")
                params.append(end_date)
            if customer_id is not None:
                conditions.append("i.customer_id = ?")
                params.append(customer_id)
            where = (" AND ".join(conditions)) if conditions else "1=1"
            rows = conn.execute(
                f"SELECT i.id, i.invoice_date, i.subtotal, i.vat_amount, i.grand_total, "
                f"i.customer_id, c.name as customer_name "
                f"FROM invoices i LEFT JOIN customers c ON i.customer_id = c.id "
                f"WHERE {where} ORDER BY i.invoice_date DESC LIMIT 200",
                params,
            ).fetchall()
            return [{
                "id": r["id"], "date": r["invoice_date"], "subtotal": r["subtotal"],
                "vat": r["vat_amount"], "total": r["grand_total"],
                "customer_id": r["customer_id"], "customer_name": r["customer_name"] or "",
            } for r in rows]
        except sqlite3.Error as e:
            logging.error("Error fetching invoices: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def get_invoice_items(self, invoice_id: str) -> List[Dict]:
        """Return line items for a given invoice."""
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT barcode, name, quantity, price FROM invoice_items "
                "WHERE invoice_id = ? ORDER BY id ASC",
                (invoice_id,),
            ).fetchall()
            return [{"barcode": r["barcode"], "name": r["name"], "quantity": r["quantity"], "price": r["price"]} for r in rows]
        except sqlite3.Error as e:
            logging.error("Error fetching invoice items for '%s': %s", invoice_id, e)
            return []
        finally:
            if conn:
                conn.close()

    # =================================================================
    # LIVE DASHBOARD ANALYTICS
    # =================================================================
    def get_dashboard_analytics(self) -> Dict:
        """Return aggregated analytics: today's revenue, VAT, top-5 products."""
        conn = None
        try:
            conn = self._get_connection()
            today = conn.execute(
                "SELECT COALESCE(SUM(grand_total), 0) FROM invoices "
                "WHERE date(invoice_date) = date('now')"
            ).fetchone()[0]
            vat_today = conn.execute(
                "SELECT COALESCE(SUM(vat_amount), 0) FROM invoices "
                "WHERE date(invoice_date) = date('now')"
            ).fetchone()[0]
            total_revenue = conn.execute(
                "SELECT COALESCE(SUM(grand_total), 0) FROM invoices"
            ).fetchone()[0]
            invoice_count = conn.execute(
                "SELECT COUNT(*) FROM invoices"
            ).fetchone()[0]
            top_products = conn.execute(
                "SELECT name, SUM(quantity) as total_qty, SUM(quantity * price) as total_sales "
                "FROM invoice_items GROUP BY name ORDER BY total_qty DESC LIMIT 5"
            ).fetchall()
            return {
                "revenue_today": today,
                "vat_today": vat_today,
                "total_revenue": total_revenue,
                "invoice_count": invoice_count,
                "top_products": [
                    {"name": r["name"], "qty": r["total_qty"], "sales": r["total_sales"]}
                    for r in top_products
                ],
            }
        except sqlite3.Error as e:
            logging.error("Error in get_dashboard_analytics: %s", e)
            return {"revenue_today": 0, "vat_today": 0, "total_revenue": 0, "invoice_count": 0, "top_products": []}
        finally:
            if conn:
                conn.close()

    # =========================================================================
    # Undo-support helper methods
    # =========================================================================

    def restore_product(self, data: dict) -> bool:
        """Restore a previously deleted product from captured state dict.

        Keys expected: Barcode, Name, Stock, ExpiryDate, Price, supplier_id
        (PascalCase to match ProductMaster schema column names).
        """
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price, supplier_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data.get("Barcode"),
                    data.get("Name"),
                    data.get("Stock"),
                    data.get("ExpiryDate"),
                    data.get("Price"),
                    data.get("supplier_id"),
                ),
            )
            conn.commit()
            # ── Audit trail ──
            try:
                self.log_stock_movement(
                    data.get("Barcode", ""), data.get("Name", ""),
                    0, data.get("Stock", 0),
                    reason="Επαναφορά", source="Undo")
            except Exception:
                pass
            return True
        except sqlite3.Error as e:
            logging.error("Error in restore_product: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    def get_customer_by_id(self, customer_id: int) -> dict | None:
        """Get full customer record by ID for undo state capture."""
        conn = None
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logging.error("Error in get_customer_by_id: %s", e)
            return None
        finally:
            if conn:
                conn.close()

    def get_supplier_by_id(self, supplier_id: int) -> dict | None:
        """Get full supplier record by ID for undo state capture."""
        conn = None
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM suppliers WHERE id = ?", (supplier_id,)
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logging.error("Error in get_supplier_by_id: %s", e)
            return None
        finally:
            if conn:
                conn.close()

    def delete_invoice(self, invoice_id: str) -> bool:
        """Delete an invoice. CASCADE handles related invoice_items."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error in delete_invoice: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    def restore_customer(self, data: dict) -> bool:
        """Restore a previously deleted customer from captured state."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO customers (id, name, amka, phone) VALUES (?, ?, ?, ?)",
                (
                    data.get("id"),
                    data.get("name"),
                    data.get("amka", ""),
                    data.get("phone", ""),
                ),
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error in restore_customer: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    def restore_supplier(self, data: dict) -> bool:
        """Restore a previously deleted supplier from captured state."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO suppliers (id, name, phone, email, address) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data.get("id"),
                    data.get("name"),
                    data.get("phone", ""),
                    data.get("email", ""),
                    data.get("address", ""),
                ),
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("Error in restore_supplier: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    # ── Stock Movement Audit Trail ───────────────────────────────────

    def log_stock_movement(self, barcode: str, product_name: str,
                           old_stock: int, new_stock: int, reason: str,
                           reference_id: str = None) -> bool:
        """Insert a stock movement audit record. Returns True on success."""
        conn = None
        try:
            conn = self._get_connection()
            diff = new_stock - old_stock
            conn.execute(
                "INSERT INTO stock_movements "
                "(timestamp, barcode, product_name, old_stock, new_stock, "
                " difference, reason, reference_id) "
                "VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?)",
                (barcode, product_name, old_stock, new_stock, diff,
                 reason, reference_id))
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("log_stock_movement failed: %s", e)
            return False
        finally:
            if conn:
                conn.close()

    def get_stock_movements(self, barcode: str = None, limit: int = 200,
                            offset: int = 0) -> List[Dict]:
        """Query stock movements, optionally filtered by barcode."""
        conn = None
        try:
            conn = self._get_connection()
            if barcode:
                rows = conn.execute(
                    "SELECT * FROM stock_movements WHERE barcode = ? "
                    "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (barcode, limit, offset)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM stock_movements "
                    "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (limit, offset)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logging.error("get_stock_movements failed: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def get_recent_movements(self, limit: int = 50) -> List[Dict]:
        """Get the most recent stock movements."""
        return self.get_stock_movements(limit=limit)

    # ── Backup & Restore ──────────────────────────────────────────────

    def backup_database(self, backup_dir: str = None) -> str:
        """Create a timestamped backup of the SQLite database.

        Copies the main DB file plus WAL and SHM companion files.
        Runs a WAL checkpoint first to minimise companion file sizes.
        Returns the absolute path to the backup file.
        """
        import shutil
        from datetime import datetime

        if backup_dir is None:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            backup_dir = os.path.join(desktop, "ENCOMM_Backups")

        os.makedirs(backup_dir, exist_ok=True)

        # Flush WAL to main DB before copying
        conn = None
        try:
            conn = self._get_connection()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logging.warning("WAL checkpoint before backup failed (non-fatal): %s", e)
        finally:
            if conn:
                conn.close()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"encomm_erp_backup_{ts}.db")

        shutil.copy2(self.db_path, backup_path)
        logging.info("Database backed up to: %s", backup_path)

        # Copy WAL and SHM companions if they exist
        for suffix in ("-wal", "-shm"):
            companion = self.db_path + suffix
            if os.path.exists(companion):
                shutil.copy2(companion, backup_path + suffix)

        return backup_path

    def restore_database(self, backup_path: str) -> bool:
        """Restore database from a backup file.

        Overwrites the current database and companion files.
        The application should be restarted after restore.
        Returns True on success.
        """
        import shutil

        if not os.path.exists(backup_path):
            logging.error("Backup file not found: %s", backup_path)
            return False

        if not backup_path.endswith(".db"):
            logging.error("Invalid backup file (must be .db): %s", backup_path)
            return False

        try:
            shutil.copy2(backup_path, self.db_path)
            logging.info("Database restored from: %s", backup_path)

            for suffix in ("-wal", "-shm"):
                companion_backup = backup_path + suffix
                companion_current = self.db_path + suffix
                if os.path.exists(companion_backup):
                    shutil.copy2(companion_backup, companion_current)
                elif os.path.exists(companion_current):
                    os.remove(companion_current)

            return True
        except Exception as e:
            logging.error("Database restore failed: %s", e)
            return False

    # ── Stock Movement Audit Trail ───────────────────────────────────

    def log_stock_movement(self, barcode: str, product_name: str,
                           old_stock: int, new_stock: int, reason: str,
                           source: str = "", operator: str = "Σύστημα") -> bool:
        """Log a stock change to the audit trail.

        Returns True on success, False on failure. Logging failures
        must never crash the calling operation — callers should wrap
        in try/except.
        """
        from datetime import datetime

        conn = None
        try:
            change_amount = new_stock - old_stock
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = self._get_connection()

            # Try new schema first (change_amount, source, operator)
            try:
                conn.execute(
                    """INSERT INTO stock_movements
                       (timestamp, barcode, product_name, old_stock, new_stock,
                        change_amount, reason, source, operator)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (timestamp, barcode, product_name,
                     old_stock, new_stock, change_amount,
                     reason, source, operator),
                )
            except sqlite3.Error:
                # Fallback: old schema (difference, reference_id)
                conn.execute(
                    """INSERT INTO stock_movements
                       (timestamp, barcode, product_name, old_stock, new_stock,
                        difference, reason, reference_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (timestamp, barcode, product_name,
                     old_stock, new_stock, change_amount,
                     reason, source),
                )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logging.error("log_stock_movement failed for %s: %s", barcode, e)
            return False
        finally:
            if conn:
                conn.close()

    def get_stock_movements(self, limit: int = 200, barcode: str = None,
                            reason: str = None, start_date: str = None,
                            end_date: str = None) -> List[Dict]:
        """Query the audit trail with optional filters.

        Returns list of dicts ordered by timestamp DESC.
        Handles both old (difference) and new (change_amount) schemas.
        """
        conn = None
        try:
            conn = self._get_connection()
            query = "SELECT * FROM stock_movements WHERE 1=1"
            params: list = []

            if barcode:
                query += " AND barcode LIKE ?"
                params.append(f"%{barcode}%")
            if reason:
                query += " AND reason = ?"
                params.append(reason)
            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # Normalize column: old schema uses 'difference', new uses 'change_amount'
                if d.get("change_amount") is None and "difference" in d:
                    d["change_amount"] = d["difference"]
                if d.get("source") is None and "reference_id" in d:
                    d["source"] = d.get("reference_id", "")
                result.append(d)
            return result
        except sqlite3.Error as e:
            logging.error("get_stock_movements query failed: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def get_product_movement_history(self, barcode: str, limit: int = 50) -> List[Dict]:
        """Convenience: all movements for a single product."""
        return self.get_stock_movements(barcode=barcode, limit=limit)
