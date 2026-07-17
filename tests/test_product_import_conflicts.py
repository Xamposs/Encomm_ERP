"""Tests for import conflict analysis — real XLSX + temp SQLite DB."""

import sqlite3, pytest, openpyxl
from infrastructure.product_import_conflicts import (
    analyze_import_conflicts, ImportConflictResult,
    ConflictRecord,
)
from infrastructure.product_import_preview import ImportColumnMapping


M = ImportColumnMapping("Barcode", "Name", "Stock", "Price", "Expiry")


def _xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _make_db(path, products):
    """Create a temp SQLite ProductMaster table."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ProductMaster("
        "Barcode TEXT, Name TEXT, Stock INTEGER, "
        "Price REAL, ExpiryDate TEXT, supplier_id INTEGER, "
        "vat_category INTEGER)")
    for barcode, name, stock, price, expiry in products:
        conn.execute(
            "INSERT INTO ProductMaster(Barcode, Name, Stock, Price, ExpiryDate) "
            "VALUES (?,?,?,?,?)",
            (barcode, name, stock, price, expiry if expiry else None))
    conn.commit()
    conn.close()


class TestNewBarcode:

    def test_new_barcode(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])  # empty DB
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 1
        assert r.unchanged_existing == 0
        assert r.changed_existing == 0

    def test_identical_existing(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, "2027-12-31"]])
        _make_db(db, [("A", "N", 1, 1.0, "2027-12-31")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 0
        assert r.unchanged_existing == 1
        assert r.changed_existing == 0

    def test_changed_fields(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        assert len(r.conflict_samples) == 1
        fields = r.conflict_samples[0].changed_fields
        assert "Name" in fields
        assert "Stock" in fields
        assert "Price" in fields
        assert "ExpiryDate" in fields


class TestExclusions:

    def test_duplicate_in_file_excluded(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""],
               ["A", "N", 1, 1.0, ""]])  # duplicate
        _make_db(db, [("A", "N", 1, 1.0, "")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        # Only one valid unique barcode, it's existing and unchanged
        assert r.duplicate_barcodes == 1
        assert r.unchanged_existing == 1
        assert r.new_barcodes == 0

    def test_invalid_row_excluded(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["", "N", 1, 1.0, ""],  # empty barcode
               ["B", "", 1, 1.0, ""]])  # empty name
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.invalid_rows == 2
        assert r.valid_rows == 0

    def test_full_single_field_changed(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 2, 1.0, "")])  # only stock changed
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        assert "Name" not in r.conflict_samples[0].changed_fields
        assert "Stock" in r.conflict_samples[0].changed_fields

class TestBatching:

    def test_more_than_one_batch(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = []
        db_products = []
        for i in range(600):  # more than BATCH_SIZE=500
            b = f"{i:013d}"
            rows.append([b, f"N{i}", 1, 1.0, ""])
            if i < 300:
                db_products.append((b, f"N{i}", 1, 1.0, ""))
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, db_products)
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 300
        assert r.unchanged_existing == 300
        assert r.valid_rows == 600

    def test_cancellation(self, tmp_path):
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 5
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", 1, 1.0, ""] for i in range(100)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, [])
        cancel = Cancel()
        r = analyze_import_conflicts(xp, M, db, cancel_event=cancel)
        assert r.cancelled
        assert "ακυρώθηκε" in r.error_message

    def test_db_unchanged_after_analysis(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])
        # Snapshot before
        before = sqlite3.connect(db).execute(
            "SELECT Barcode, Name, Stock, Price, ExpiryDate FROM ProductMaster"
        ).fetchall()
        analyze_import_conflicts(xp, M, db)
        after = sqlite3.connect(db).execute(
            "SELECT Barcode, Name, Stock, Price, ExpiryDate FROM ProductMaster"
        ).fetchall()
        assert before == after

    def test_no_write_sql(self):
        import inspect
        from infrastructure import product_import_conflicts as ic
        src = inspect.getsource(ic.analyze_import_conflicts)
        for pat in ["INSERT", "UPDATE", "DELETE", "DROP",
                     "ALTER", "CREATE", "REPLACE"]:
            assert pat not in src, f"Forbidden '{pat}' in conflicts"
        # Verify mode=ro in the _connect_ro helper
        src_conn = inspect.getsource(ic._connect_ro)
        assert "mode=ro" in src_conn
