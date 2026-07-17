"""Tests for atomic product import commit (Phase C1)."""

import sqlite3, pytest, openpyxl
from infrastructure.product_import_commit import (
    commit_new_products_from_xlsx, ImportCommitResult)
from infrastructure.product_import_plan import (
    ImportPlan, ImportReviewPolicy, ChangedPolicy)
from infrastructure.product_import_identity import (
    fingerprint_import_source, ImportSourceSignature)
from infrastructure.product_import_preview import ImportColumnMapping


M = ImportColumnMapping("Barcode", "Name", "Stock", "Price", "Expiry")


def _xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _make_db(path, products=None):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ProductMaster("
        "Barcode TEXT, Name TEXT, Stock INTEGER, "
        "Price REAL, ExpiryDate TEXT, supplier_id INTEGER, "
        "vat_category INTEGER DEFAULT 13)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_movements("
        "id INTEGER PRIMARY KEY, barcode TEXT, product_name TEXT, "
        "old_stock INTEGER, new_stock INTEGER, "
        "change_amount INTEGER, reason TEXT, source TEXT, "
        "operator TEXT, timestamp TEXT)")
    if products:
        for b, n, s, p, e in products:
            conn.execute(
                "INSERT INTO ProductMaster"
                "(Barcode, Name, Stock, Price, ExpiryDate) "
                "VALUES (?,?,?,?,?)", (b, n, s, p, e))
    conn.commit()
    conn.close()


def _plan(xp, mapping, db_path, **kw):
    from infrastructure.product_import_conflicts import analyze_import_conflicts
    from infrastructure.product_import_plan import build_import_plan
    r = analyze_import_conflicts(xp, mapping, db_path)
    assert r.ok
    return build_import_plan(r, ImportReviewPolicy(**kw))


class TestHappyPath:

    def test_inserts_new_only(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewProduct", 10, 5.0, "2027-12-31"]])
        _make_db(db)
        plan = _plan(xp, M, db)
        assert plan.planned_new == 1
        sig = fingerprint_import_source(xp, M)
        plan_with_sig = ImportPlan(
            read_only=True, file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=1, classified_rows=1,
            planned_new=1, source_signature=sig)

        r = commit_new_products_from_xlsx(xp, M, plan_with_sig, db)
        assert r.ok
        assert r.inserted_rows == 1

        # Verify in DB
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT Barcode, Name, Stock, Price, ExpiryDate "
            "FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert row["Name"] == "NewProduct"
        # VAT should be DB default (13), not set by commit
        vat = conn.execute(
            "SELECT vat_category FROM ProductMaster WHERE Barcode='A'"
        ).fetchone()[0]
        assert vat == 13, f"VAT should be DB default 13, got {vat}"
        conn.close()

    def test_identical_existing_unchanged(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        plan = _plan(xp, M, db)
        assert plan.skipped_identical == 1
        assert plan.planned_new == 0

    def test_audit_rows_created(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NP", 5, 1.0, ""]])
        _make_db(db)
        plan = _plan(xp, M, db)
        sig = fingerprint_import_source(xp, M)
        plan_with_sig = ImportPlan(
            read_only=True, file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=1, classified_rows=1,
            planned_new=1, source_signature=sig)

        r = commit_new_products_from_xlsx(xp, M, plan_with_sig, db)
        assert r.ok

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        aud = conn.execute(
            "SELECT * FROM stock_movements WHERE barcode='A'").fetchone()
        assert aud is not None
        assert aud["old_stock"] == 0
        assert aud["new_stock"] == 5
        assert aud["change_amount"] == 5
        assert "Εισαγωγή Excel" in aud["reason"]
        assert aud["source"] == "Excel Import"
        conn.close()


class TestRollback:

    def test_cancellation_rolls_back(self, tmp_path):
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 3

        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", 1, 1.0, ""] for i in range(100)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db)
        plan = _plan(xp, M, db)
        sig = fingerprint_import_source(xp, M)
        plan_with_sig = ImportPlan(
            read_only=True, file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=100, classified_rows=100,
            planned_new=100, source_signature=sig)

        cancel = Cancel()
        r = commit_new_products_from_xlsx(
            xp, M, plan_with_sig, db, cancel_event=cancel)
        assert r.cancelled
        assert r.inserted_rows == 0

        conn = sqlite3.connect(db)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM ProductMaster").fetchone()[0]
        assert cnt == 0
        aud_cnt = conn.execute(
            "SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert aud_cnt == 0
        conn.close()

    def test_source_changed_after_plan_no_writes(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db)
        # Use a stale signature from a different file
        fake_sig = ImportSourceSignature(
            file_size_bytes=1, file_sha256="x"*64, mapping_sha256="y"*64)
        plan = ImportPlan(
            read_only=True, planned_new=1, source_signature=fake_sig)

        r = commit_new_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "άλλαξε" in r.error_message

        conn = sqlite3.connect(db)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM ProductMaster").fetchone()[0]
        assert cnt == 0
        conn.close()

    def test_db_state_changed_no_writes(self, tmp_path):
        """Product was new during analysis but inserted before commit."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db)
        plan = _plan(xp, M, db)  # A is new
        sig = fingerprint_import_source(xp, M)
        plan_with_sig = ImportPlan(
            read_only=True, file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=1, classified_rows=1,
            planned_new=1, source_signature=sig)

        # Someone inserts A before commit
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ProductMaster(Barcode,Name,Stock,Price) "
                     "VALUES ('A','Other',1,1.0)")
        conn.commit()
        conn.close()

        r = commit_new_products_from_xlsx(xp, M, plan_with_sig, db)
        assert not r.ok
        # A is no longer new; should fail stale-plan check

    def test_no_write_sql_in_commit(self):
        import inspect
        from infrastructure import product_import_commit as pic
        src = inspect.getsource(pic.commit_new_products_from_xlsx)
        for pat in ["UPDATE ", "DELETE FROM", "REPLACE", "UPSERT",
                     "ON CONFLICT"]:
            assert pat not in src, f"Forbidden '{pat}' in commit service"

    def test_duplicate_barcode_one_invalid_b1_c1_atomic(self, tmp_path):
        """Real B1 plan through duplicate A, A, B → valid_rows=2, invalid=1,
        dupes=1; C1 inserts A and B atomically."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N1", 1, 1.0, ""],
               ["A", "N1", 1, 1.0, ""],
               ["B", "N2", 1, 1.0, ""]])
        _make_db(db)

        # B1 analysis with real plan
        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.valid_rows == 2
        assert plan.invalid_rows == 1
        assert plan.duplicate_barcodes == 1
        assert plan.planned_new == 2

        sig = fingerprint_import_source(xp, M)
        # Build plan with source signature
        plan_with_sig = ImportPlan(
            read_only=True,
            file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=plan.valid_rows,
            invalid_rows=plan.invalid_rows,
            duplicate_barcodes=plan.duplicate_barcodes,
            classified_rows=plan.classified_rows,
            planned_new=plan.planned_new,
            skipped_identical=plan.skipped_identical,
            manual_review=plan.manual_review,
            skipped_changed=plan.skipped_changed,
            rejected_invalid=plan.rejected_invalid,
            skipped_duplicates=plan.skipped_duplicates,
            source_signature=sig,
        )
        r = commit_new_products_from_xlsx(xp, M, plan_with_sig, db)
        assert r.ok
        assert r.inserted_rows == 2

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cnt = conn.execute(
            "SELECT COUNT(*) c FROM ProductMaster").fetchone()["c"]
        assert cnt == 2
        conn.close()

    def test_audit_uses_log_stock_movement_on_conn(self, tmp_path):
        """Prove _log_stock_movement_on_conn is called (not raw INSERT)."""
        import inspect
        from infrastructure import product_import_commit as pic
        src = inspect.getsource(pic.commit_new_products_from_xlsx)
        assert "_log_stock_movement_on_conn" in src

    def test_missing_db_path_fails(self, tmp_path):
        """Non-existent db path fails without creating file."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "nonexistent.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        sig = ImportSourceSignature(file_size_bytes=100,
                                     file_sha256="a"*64, mapping_sha256="b"*64)
        plan = ImportPlan(read_only=True, planned_new=1, source_signature=sig)
        r = commit_new_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "δεν βρέθηκε" in r.error_message.lower()
        import os
        assert not os.path.exists(db)


class TestB1C1Parity:

    def test_b1_reports_dupe_then_c1_inserts_unique(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NA", 1, 1.0, ""],
               ["A", "NA", 1, 1.0, ""],  # duplicate
               ["B", "NB", 1, 1.0, ""]])
        _make_db(db)
        from infrastructure.product_import_conflicts import analyze_import_conflicts
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.valid_rows == 2
        assert r.invalid_rows == 1
        assert r.duplicate_barcodes == 1
        from infrastructure.product_import_plan import build_import_plan
        plan = build_import_plan(r, ImportReviewPolicy())
        assert plan.planned_new == 2
        sig = fingerprint_import_source(xp, M)
        plan = ImportPlan(
            read_only=True, file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=2, invalid_rows=1, duplicate_barcodes=1,
            classified_rows=2, planned_new=2, rejected_invalid=1,
            skipped_duplicates=1, source_signature=sig)
        res = commit_new_products_from_xlsx(xp, M, plan, db)
        assert res.ok
        assert res.inserted_rows == 2
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        cnt = conn.execute(
            "SELECT COUNT(*) c FROM ProductMaster").fetchone()["c"]
        assert cnt == 2

    def test_decimal_price_parity_changed_new(self, tmp_path):
        """Existing price 1.0 vs XLSX price 1.00005 + new product:
        B1 and C1 agree existing is changed; new product is inserted."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["EX", "Existing", 5, 1.00005, ""],
               ["NW", "NewOne", 3, 2.0, ""]])
        _make_db(db, products=[("EX", "Existing", 5, 1.0, "")])

        # B1 analysis
        from infrastructure.product_import_conflicts import analyze_import_conflicts
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.new_barcodes == 1
        assert r.changed_existing == 1

        # Build real plan from B1 result
        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        sig = fingerprint_import_source(xp, M)
        plan_with_sig = ImportPlan(
            read_only=True,
            file_name=plan.file_name,
            sheet_name=plan.sheet_name,
            valid_rows=plan.valid_rows,
            invalid_rows=plan.invalid_rows,
            duplicate_barcodes=plan.duplicate_barcodes,
            classified_rows=plan.classified_rows,
            planned_new=plan.planned_new,
            skipped_identical=plan.skipped_identical,
            manual_review=plan.manual_review,
            skipped_changed=plan.skipped_changed,
            rejected_invalid=plan.rejected_invalid,
            skipped_duplicates=plan.skipped_duplicates,
            source_signature=sig,
        )

        res = commit_new_products_from_xlsx(xp, M, plan_with_sig, db)
        assert res.ok
        assert res.inserted_rows == 1

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        nw = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='NW'").fetchone()
        assert nw is not None
        ex = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='EX'").fetchone()
        assert ex["Price"] == 1.0
        conn.close()


class TestLimitAndSafety:

    def test_over_limit_zero_inserts(self, tmp_path, monkeypatch):
        import infrastructure.product_import_commit as pic
        monkeypatch.setattr(pic, "MAX_IMPORT_ROWS", 2)
        monkeypatch.setattr(
            "infrastructure.product_import_conflicts.MAX_IMPORT_ROWS", 2)
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NA", 1, 1.0, ""],
               ["B", "NB", 1, 1.0, ""],
               ["C", "NC", 1, 1.0, ""]])
        _make_db(db)
        sig = fingerprint_import_source(xp, M)
        plan = ImportPlan(
            read_only=True, planned_new=3, source_signature=sig,
            valid_rows=3, invalid_rows=0, duplicate_barcodes=0,
            classified_rows=3)
        res = commit_new_products_from_xlsx(xp, M, plan, db)
        assert not res.ok
        assert res.inserted_rows == 0
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) c FROM ProductMaster").fetchone()[0]
        assert cnt == 0
        aud = conn.execute(
            "SELECT COUNT(*) c FROM stock_movements").fetchone()[0]
        assert aud == 0

    def test_audit_failure_rolls_back(self, tmp_path, monkeypatch):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NA", 1, 1.0, ""]])
        _make_db(db)
        sig = fingerprint_import_source(xp, M)
        plan = ImportPlan(
            read_only=True, planned_new=1, source_signature=sig,
            valid_rows=1, invalid_rows=0, duplicate_barcodes=0,
            classified_rows=1)
        from infrastructure import database_service
        def _boom(*a, **k):
            raise RuntimeError("audit down")
        monkeypatch.setattr(
            database_service.DatabaseService,
            "_log_stock_movement_on_conn", staticmethod(_boom))
        res = commit_new_products_from_xlsx(xp, M, plan, db)
        assert not res.ok
        assert res.inserted_rows == 0
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) c FROM ProductMaster").fetchone()[0]
        assert cnt == 0

    def test_foreign_keys_enabled(self, tmp_path, monkeypatch):
        import infrastructure.product_import_commit as pic
        import unittest.mock as mock
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NA", 1, 1.0, ""]])
        _make_db(db)
        sig = fingerprint_import_source(xp, M)
        plan = ImportPlan(
            read_only=True, planned_new=1, source_signature=sig,
            valid_rows=1, invalid_rows=0, duplicate_barcodes=0,
            classified_rows=1)
        captured = {}
        orig = pic._connect_rw
        def spy(path):
            c = orig(path)
            captured["fk"] = c.execute("PRAGMA foreign_keys").fetchone()[0]
            return c
        with mock.patch.object(pic, "_connect_rw", spy):
            commit_new_products_from_xlsx(xp, M, plan, db)
        assert captured["fk"] == 1

    def test_missing_columns_fail_safe(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NA", 1, 1.0, ""]])
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE ProductMaster(Barcode TEXT, Name TEXT)")
        conn.commit(); conn.close()
        sig = fingerprint_import_source(xp, M)
        plan = ImportPlan(
            read_only=True, planned_new=1, source_signature=sig,
            valid_rows=1, invalid_rows=0, duplicate_barcodes=0,
            classified_rows=1)
        res = commit_new_products_from_xlsx(xp, M, plan, db)
        assert not res.ok
        conn2 = sqlite3.connect(db)
        cnt = conn2.execute("SELECT COUNT(*) c FROM ProductMaster").fetchone()[0]
        assert cnt == 0
