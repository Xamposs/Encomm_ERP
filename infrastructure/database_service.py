import sqlite3
import os
import logging
from typing import List, Optional, Dict, Tuple
from core.domain_models import Product

class DatabaseService:
    def __init__(self, db_path: str = "pharmacy.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Establish a connection to the SQLite database with Row factory enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # PRAGMA optimizations for high-performance bulk operations
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
            conn.commit()

            # Performance indexes for 100K+ row queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_name ON ProductMaster(Name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_stock ON ProductMaster(Stock)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_expiry ON ProductMaster(ExpiryDate)")
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
            ("8801234567890", "Paracetamol 500mg (Panadol)", 150, "2027-08-15", 3.50),
            ("8801234567891", "Amoxicillin 250mg (Antibiotic)", 8, "2026-06-10", 12.99), # Low stock & Near expiry (rel. to June 2026)
            ("8801234567892", "Ibuprofen 400mg (Advil)", 80, "2027-01-20", 5.25),
            ("8801234567893", "Atorvastatin 20mg (Lipitor)", 4, "2026-05-30", 25.00), # Low stock & Expired
            ("8801234567894", "Metformin 850mg (Glucophage)", 200, "2028-11-05", 9.80),
            ("8801234567895", "Omeprazole 20mg (Prilosec)", 12, "2026-06-22", 8.45), # Near expiry
            ("8801234567896", "Lisinopril 10mg (Zestril)", 95, "2027-04-12", 11.20),
            ("8801234567897", "Cetirizine 10mg (Zyrtec)", 180, "2028-02-18", 4.99),
        ]
        cursor.executemany("""
            INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price)
            VALUES (?, ?, ?, ?, ?)
        """, dummy_products)

    def get_all_products(self) -> List[Product]:
        """Fetch all product records from ProductMaster, ordered by name."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT Barcode, Name, Stock, ExpiryDate, Price FROM ProductMaster ORDER BY Name ASC")
            rows = cursor.fetchall()
            products = [
                Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"]
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
                "SELECT Barcode, Name, Stock, ExpiryDate, Price "
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
            cursor.execute("SELECT Barcode, Name, Stock, ExpiryDate, Price FROM ProductMaster WHERE Barcode = ?", (barcode,))
            row = cursor.fetchone()
            product = None
            if row:
                product = Product(
                    barcode=row["Barcode"],
                    name=row["Name"],
                    stock=row["Stock"],
                    expiry_date=row["ExpiryDate"],
                    price=row["Price"]
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
            cursor.execute("""
                INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price)
                VALUES (?, ?, ?, ?, ?)
            """, (product.barcode, product.name, product.stock, product.expiry_date, product.price))
            conn.commit()
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
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE ProductMaster
                SET Name = ?, Stock = ?, ExpiryDate = ?, Price = ?
                WHERE Barcode = ?
            """, (product.name, product.stock, product.expiry_date, product.price, product.barcode))
            conn.commit()
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
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE ProductMaster
                SET Stock = ?
                WHERE Barcode = ?
            """, (new_stock, barcode))
            conn.commit()
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
                    INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(Barcode) DO UPDATE SET
                        Name       = excluded.Name,
                        Stock      = Stock + excluded.Stock,
                        ExpiryDate = excluded.ExpiryDate,
                        Price      = excluded.Price
                    """,
                    products_list,
                )
                conn.commit()
                logging.info(f"Bulk upsert completed for {len(products_list)} items.")
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
        """Return up to `limit` most critical products sorted by severity (expired → near-expiry → low-stock)."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT Barcode, Name, Stock, ExpiryDate, Price,
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
                f"SELECT Barcode, Name, Stock, ExpiryDate, Price "
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
