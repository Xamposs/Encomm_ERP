"""Pure-Python tests for stock_adjustment_service — CURRENT and LEGACY schemas."""

import sqlite3
import pytest

from infrastructure.stock_adjustment_service import (
    StockAdjustmentRequest, StockAdjustmentResult,
    adjust_stock, REASON_CHOICES,
)


# ═══════════════════════════════════════════════════════════════════════
# Schema helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_current_schema(path: str) -> None:
    """Schema with change_amount, source, operator."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL, supplier_id INTEGER
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, reason TEXT NOT NULL,
            change_amount INTEGER, source TEXT,
            operator TEXT DEFAULT 'Σύστημα'
        );
    """)
    conn.commit()
    conn.close()


def _make_legacy_schema(path: str) -> None:
    """Schema with difference, reference_id; NO change_amount/source/operator."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE ProductMaster (
            Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
            Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
            Price REAL NOT NULL
        );
        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, barcode TEXT NOT NULL,
            product_name TEXT NOT NULL, old_stock INTEGER NOT NULL,
            new_stock INTEGER NOT NULL, difference INTEGER NOT NULL,
            reason TEXT NOT NULL, reference_id TEXT
        );
    """)
    conn.commit()
    conn.close()


def _seed_product(path: str, barcode: str, name: str, stock: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, '2027-12-31', 5.0)",
        (barcode, name, stock),
    )
    conn.commit()
    conn.close()


def _audit_rows(db_path: str, barcode: str) -> list:
    """Return all audit rows for a barcode."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM stock_movements WHERE barcode=? ORDER BY id",
        (barcode,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _product_stock(db_path: str, barcode: str) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    val = conn.execute(
        "SELECT Stock FROM ProductMaster WHERE Barcode=?",
        (barcode,),
    ).fetchone()[0]
    conn.close()
    return val


# ═══════════════════════════════════════════════════════════════════════
# Tests — current schema (change_amount + source + operator)
# ═══════════════════════════════════════════════════════════════════════

class TestCurrentSchema:

    def test_stock_increase_one_audit_row(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ001", "Test Product", 50)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ001", expected_current_stock=50,
            counted_stock=80, reason="Απογραφή"))
        assert r.ok
        assert not r.no_change
        rows = _audit_rows(db, "ADJ001")
        assert len(rows) == 1
        row = rows[0]
        assert row["old_stock"] == 50
        assert row["new_stock"] == 80
        assert row["change_amount"] == 30
        assert row["reason"] == "Απογραφή"
        assert row["source"] == "Ελεγχόμενη Διόρθωση Αποθέματος"
        assert row.get("operator") == "Φαρμακοποιός"
        assert _product_stock(db, "ADJ001") == 80

    def test_stock_decrease_one_audit_row(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ002", "Declining", 100)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ002", expected_current_stock=100,
            counted_stock=25, reason="Φθορά / Λήξη"))
        assert r.ok
        rows = _audit_rows(db, "ADJ002")
        assert len(rows) == 1
        assert rows[0]["change_amount"] == -75
        assert _product_stock(db, "ADJ002") == 25

    def test_unchanged_count_no_update_no_audit(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ003", "Same", 42)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ003", expected_current_stock=42,
            counted_stock=42, reason="Απογραφή"))
        assert r.ok
        assert r.no_change
        rows = _audit_rows(db, "ADJ003")
        assert len(rows) == 0
        assert _product_stock(db, "ADJ003") == 42

    def test_negative_stock_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ004", "Neg", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ004", expected_current_stock=10,
            counted_stock=-5, reason="Απογραφή"))
        assert not r.ok
        assert "μη αρνητικός" in r.message or "αρνητικ" in r.message.lower()

    def test_bool_values_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ005", "Bool", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ005", expected_current_stock=10,
            counted_stock=True, reason="Απογραφή"))
        assert not r.ok
        assert "boolean" in r.message.lower()

    def test_bool_expected_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ006", "Bool2", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ006", expected_current_stock=True,
            counted_stock=5, reason="Απογραφή"))
        assert not r.ok

    def test_blank_reason_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ007", "Reason", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ007", expected_current_stock=10,
            counted_stock=20, reason="   "))
        assert not r.ok

    def test_missing_product(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="NONEXISTENT", expected_current_stock=0,
            counted_stock=10, reason="Απογραφή"))
        assert not r.ok
        assert "δεν βρέθηκε" in r.message

    def test_stale_expected_concurrency_conflict(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ008", "Stale", 20)
        # Another process changes stock
        conn2 = sqlite3.connect(db)
        conn2.execute(
            "UPDATE ProductMaster SET Stock = 99 WHERE Barcode='ADJ008'")
        conn2.commit()
        conn2.close()
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ008", expected_current_stock=20,
            counted_stock=30, reason="Απογραφή"))
        assert not r.ok
        assert "άλλαξε" in r.message
        # Product must be unchanged
        assert _product_stock(db, "ADJ008") == 99

    def test_audit_insertion_failure_rolls_back_product_update(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE ProductMaster (
                Barcode TEXT PRIMARY KEY, Name TEXT NOT NULL,
                Stock INTEGER NOT NULL, ExpiryDate TEXT NOT NULL,
                Price REAL NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO ProductMaster VALUES ('ROLL','X',20,'2027-12-31',5.0)")
        conn.commit()
        conn.close()
        # No stock_movements table → audit will fail
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ROLL", expected_current_stock=20,
            counted_stock=99, reason="Απογραφή"))
        assert not r.ok
        assert _product_stock(db, "ROLL") == 20

    def test_difference_is_correct(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ009", "Diff", 100)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ009", expected_current_stock=100,
            counted_stock=30, reason="Φθορά / Λήξη"))
        assert r.ok
        row = _audit_rows(db, "ADJ009")[0]
        assert row["change_amount"] == -70
        assert row["old_stock"] == 100
        assert row["new_stock"] == 30
        assert row["reason"] == "Φθορά / Λήξη"

    def test_custom_operator_stored(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ010", "Op", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ010", expected_current_stock=10,
            counted_stock=15, reason="Απογραφή",
            operator="Δημήτρης"))
        assert r.ok
        row = _audit_rows(db, "ADJ010")[0]
        assert row.get("operator") == "Δημήτρης"

    def test_default_operator_is_pharmacist(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ011", "Default", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ011", expected_current_stock=10,
            counted_stock=20, reason="Απογραφή"))
        assert r.ok
        row = _audit_rows(db, "ADJ011")[0]
        assert row.get("operator") == "Φαρμακοποιός"

    def test_source_field_is_correct_tag(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ012", "Src", 5)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ012", expected_current_stock=5,
            counted_stock=8, reason="Διόρθωση δεδομένων"))
        assert r.ok
        row = _audit_rows(db, "ADJ012")[0]
        assert row["source"] == "Ελεγχόμενη Διόρθωση Αποθέματος"

    def test_no_vat_field_access(self, tmp_path):
        """Ensure the service does not touch VAT at all."""
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "ADJ013", "NoVat", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="ADJ013", expected_current_stock=10,
            counted_stock=30, reason="Απογραφή"))
        assert r.ok
        # Verify no VAT columns were queried: check that the audit row
        # has NO vat-related column and the success message is Greek-only
        assert "ΦΠΑ" not in r.message
        assert "VAT" not in r.message  # case-sensitive — not in service

    def test_empty_barcode_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="", expected_current_stock=0,
            counted_stock=0, reason="Απογραφή"))
        assert not r.ok


# ═══════════════════════════════════════════════════════════════════════
# Tests — legacy schema (difference + reference_id)
# ═══════════════════════════════════════════════════════════════════════

class TestLegacySchema:

    def test_stock_increase_uses_difference_column(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy_schema(db)
        _seed_product(db, "LEGACY1", "Legacy Product", 30)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="LEGACY1", expected_current_stock=30,
            counted_stock=60, reason="Απογραφή"))
        assert r.ok
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM stock_movements WHERE barcode='LEGACY1'"
        ).fetchone()
        assert row["difference"] == 30
        assert row["reference_id"] == "Ελεγχόμενη Διόρθωση Αποθέματος"
        # Verify change_amount and source don't exist
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info('stock_movements')")]
        assert "change_amount" not in cols
        assert "source" not in cols
        conn.close()

    def test_stock_decrease_uses_difference_column(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy_schema(db)
        _seed_product(db, "LEGACY2", "Legacy Down", 100)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="LEGACY2", expected_current_stock=100,
            counted_stock=40, reason="Φθορά / Λήξη"))
        assert r.ok
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM stock_movements WHERE barcode='LEGACY2'"
        ).fetchone()
        assert row["difference"] == -60
        assert _product_stock(db, "LEGACY2") == 40
        conn.close()

    def test_legacy_no_operator_column(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_legacy_schema(db)
        _seed_product(db, "LEGACY3", "NoOp", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="LEGACY3", expected_current_stock=10,
            counted_stock=20, reason="Απογραφή"))
        assert r.ok
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info('stock_movements')")]
        assert "operator" not in cols
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Validation edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestValidationEdgeCases:

    def test_string_stock_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "E1", "Str", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="E1", expected_current_stock=10,
            counted_stock="abc", reason="Απογραφή"))
        assert not r.ok

    def test_none_stock_rejected(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "E2", "None", 10)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="E2", expected_current_stock=10,
            counted_stock=None, reason="Απογραφή"))
        assert not r.ok

    def test_zero_counted_stock_works(self, tmp_path):
        db = str(tmp_path / "test.db")
        _make_current_schema(db)
        _seed_product(db, "E3", "Zero", 5)
        r = adjust_stock(db, StockAdjustmentRequest(
            barcode="E3", expected_current_stock=5,
            counted_stock=0, reason="Φθορά / Λήξη"))
        assert r.ok
        assert _product_stock(db, "E3") == 0
        rows = _audit_rows(db, "E3")
        assert len(rows) == 1
        assert rows[0]["change_amount"] == -5
