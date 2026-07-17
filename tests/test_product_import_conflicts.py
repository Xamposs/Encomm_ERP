"""Tests for import conflict analysis — real XLSX + temp SQLite DB."""

import sqlite3, pytest, openpyxl
from infrastructure.product_import_conflicts import (
    analyze_import_conflicts, ImportConflictResult,
    ConflictRecord,
)
from infrastructure.product_import_preview import ImportColumnMapping


M = ImportColumnMapping("Barcode", "Name", "Stock", "Price", "Expiry")
M_DUP = ImportColumnMapping("X", "X", "Stock", "Price", "Expiry")  # duplicate


def _xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _make_db(path, products):
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


class TestMapping:

    def test_duplicate_mapping_rejected(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        _xlsx(xp, ["X", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        db = str(tmp_path / "t.db")
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M_DUP, db)
        assert not r.ok
        assert "μοναδική" in r.error_message


class TestClassification:

    def test_new_barcode(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 1
        assert r.source_signature is not None
        assert len(r.source_signature.file_sha256) == 64
        assert r.classified_rows == r.valid_rows == 1

    def test_identical_existing(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, "2027-12-31"]])
        _make_db(db, [("A", "N", 1, 1.0, "2027-12-31")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.unchanged_existing == 1
        assert r.classified_rows == r.valid_rows == 1

    def test_changed_fields(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        assert r.classified_rows == 1
        fields = r.conflict_samples[0].changed_fields
        assert "Name" in fields
        assert "Stock" in fields
        assert "Price" in fields
        assert "ExpiryDate" in fields

    def test_small_price_diff_still_conflict(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 0.0005, ""]])
        _make_db(db, [("A", "N", 1, 0.0004, "")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        assert "Price" in r.conflict_samples[0].changed_fields


class TestExclusions:

    def test_duplicate_in_file_excluded(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""],
               ["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.duplicate_barcodes == 1
        assert r.unchanged_existing == 1
        assert r.classified_rows == 1

    def test_invalid_row_excluded(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["", "N", 1, 1.0, ""],
               ["B", "", 1, 1.0, ""]])
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.invalid_rows == 2
        assert r.classified_rows == 0


class TestBatching:

    def test_incremental_batching(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = []
        db_products = []
        for i in range(600):
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
        assert r.classified_rows == 600


class TestLimits:

    def test_max_rows_partial(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", 1, 1.0, ""] for i in range(20)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db, max_rows=10)
        assert not r.ok
        assert not r.cancelled
        assert "Υπέρβαση" in r.error_message
        assert r.scanned_rows == 10
        assert r.classified_rows == 10

    def test_cancellation_before_batch_flush(self, tmp_path):
        """Cancel during scanning before batch reaches 500:
        classified_rows < valid_rows, sum equals classified."""
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 150  # cancel after 150 rows, before batch full
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", 1, 1.0, ""] for i in range(200)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, [])
        cancel = Cancel()
        r = analyze_import_conflicts(xp, M, db, cancel_event=cancel)
        assert r.cancelled
        # Pending batch (remaining 50) not classified
        assert r.classified_rows < r.valid_rows
        assert r.classified_rows == (
            r.new_barcodes + r.unchanged_existing + r.changed_existing)

    def test_cancellation_during_classify_batch(self, tmp_path):
        """Cancel during DB classification of a full batch:
        exactly cancelled, classified < valid, sum equals classified."""
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 600
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", 1, 1.0, ""] for i in range(800)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, [])
        cancel = Cancel()
        r = analyze_import_conflicts(xp, M, db, cancel_event=cancel)
        assert r.cancelled, "Should be cancelled mid-batch"
        assert r.classified_rows > 0, "Some rows must be classified"
        assert r.classified_rows < r.valid_rows, (
            "Not all valid rows classified")
        assert r.classified_rows == (
            r.new_barcodes + r.unchanged_existing + r.changed_existing)


class TestRobustness:

    def test_db_path_with_spaces(self, tmp_path):
        db_dir = tmp_path / "some dir with spaces"
        db_dir.mkdir()
        xp = str(tmp_path / "t.xlsx")
        db = str(db_dir / "my db.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 1
        assert r.source_signature is not None
        assert len(r.source_signature.file_sha256) == 64

    def test_db_unchanged_after_analysis(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])
        before = sqlite3.connect(db).execute(
            "SELECT Barcode, Name, Stock, Price, ExpiryDate FROM ProductMaster"
        ).fetchall()
        analyze_import_conflicts(xp, M, db)
        after = sqlite3.connect(db).execute(
            "SELECT Barcode, Name, Stock, Price, ExpiryDate FROM ProductMaster"
        ).fetchall()
        assert before == after

    def test_signature_mismatch_fails_analysis(self, tmp_path, monkeypatch):
        """Monkeypatch verify to return False → analysis fails with Greek msg."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])
        from infrastructure import product_import_conflicts as ic
        monkeypatch.setattr(ic, "verify_import_source", lambda *a, **kw: False)
        r = analyze_import_conflicts(xp, M, db)
        assert not r.ok
        assert r.source_signature is None
        assert "άλλαξε" in r.error_message

    def test_no_write_sql(self):
        import inspect
        from infrastructure import product_import_conflicts as ic
        src = inspect.getsource(ic.analyze_import_conflicts)
        for pat in ["INSERT", "UPDATE", "DELETE", "DROP",
                     "ALTER", "CREATE", "REPLACE"]:
            assert pat not in src, f"Forbidden '{pat}' in conflicts"
        src_conn = inspect.getsource(ic._connect_ro)
        assert "mode=ro" in src_conn

    def test_uri_safe_from_source(self):
        import inspect
        from infrastructure import product_import_conflicts as ic
        src = inspect.getsource(ic._connect_ro)
        assert "Path(" in src


class TestC2Details:
    """Phase C2: read-only detail rows for changed existing products."""

    def test_multi_field_changes_produce_detail_rows(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        details = r.conflict_details
        assert len(details) >= 1
        fields_seen = {d.field for d in details}
        assert "Name" in fields_seen
        assert "Stock" in fields_seen
        assert "Price" in fields_seen
        assert "ExpiryDate" in fields_seen
        # Verify actual values
        name_d = [d for d in details if d.field == "Name"][0]
        assert name_d.barcode == "A"
        assert name_d.current_value == "OldName"
        assert name_d.incoming_value == "NewName"

    def test_identical_existing_produces_no_details(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, "2027-12-31"]])
        _make_db(db, [("A", "N", 1, 1.0, "2027-12-31")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.unchanged_existing == 1
        assert len(r.conflict_details) == 0

    def test_details_bounded(self, tmp_path):
        """Detail rows bounded at MAX_CONFLICT_DETAILS (200)."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        # 100 products, each with 3 changed fields → 300 detail rows
        xls_rows = []
        db_rows = []
        for i in range(100):
            b = f"{i:013d}"
            xls_rows.append([b, f"N{i}", i + 10, float(i) + 1.0, "2029-01-01"])
            db_rows.append((b, f"O{i}", i, float(i), "2028-01-01"))
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], xls_rows)
        _make_db(db, db_rows)
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 100
        # Should be bounded at 200 or fewer
        assert len(r.conflict_details) <= 200

    def test_cancellation_preserves_details_not_misleading(self, tmp_path):
        """Cancellation returns what was classified; details reflect partial."""
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 10

        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", i + 1, float(i), "2029-01-01"]
                for i in range(50)]
        db_r = [(f"{i:013d}", f"O{i}", i, float(i), "2028-01-01")
                for i in range(50)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, db_r)
        cancel = Cancel()
        r = analyze_import_conflicts(xp, M, db, cancel_event=cancel)
        assert r.cancelled
        # Details must be consistent with classified counts
        assert r.changed_existing == (
            r.classified_rows - r.new_barcodes - r.unchanged_existing)

    def test_no_write_sql_in_c2_path(self):
        import inspect
        from infrastructure import product_import_conflicts as ic
        src = inspect.getsource(ic._classify_batch)
        for pat in ["INSERT", "UPDATE", "DELETE", "DROP",
                     "ALTER", "CREATE", "REPLACE"]:
            assert pat not in src, f"Forbidden '{pat}' in _classify_batch"

    def test_blank_values_formatted(self, tmp_path):
        """Blank/invalid values formatted as '—'."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "2028-01-01")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        exp_d = [d for d in r.conflict_details if d.field == "ExpiryDate"]
        assert len(exp_d) == 1
        assert exp_d[0].current_value == "2028-01-01"
        # Incoming expiry is blank → formatted as '—'
        assert exp_d[0].incoming_value == "—"

    def test_existing_b1_tests_still_pass(self, tmp_path):
        """Sanity: existing B1 invariants untouched by C2 extension."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 1
        assert r.valid_rows == 1
        assert r.classified_rows == 1
        assert r.conflict_details == ()
