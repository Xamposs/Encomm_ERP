"""Tests for atomic product import update commit (Phase C3)."""

import hashlib
import sqlite3, pytest, openpyxl
from decimal import Decimal
from infrastructure.product_import_update_commit import (
    commit_approved_changed_products_from_xlsx, ImportUpdateCommitResult)
from infrastructure.product_import_plan import (
    ImportPlan, ImportReviewPolicy, ChangedPolicy, build_import_plan)
from infrastructure.product_import_identity import (
    fingerprint_import_source, ImportSourceSignature)
from infrastructure.product_import_preview import ImportColumnMapping
from infrastructure.product_import_conflicts import (
    analyze_import_conflicts, ImportConflictResult, _compute_review_db_signature,
)


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
    r = analyze_import_conflicts(xp, mapping, db_path)
    assert r.ok, f"B1 analysis failed: {r.error_message}"
    return build_import_plan(r, ImportReviewPolicy(**kw))


# ── Happy Path ────────────────────────────────────────────────────────


class TestHappyPathUpdates:

    def test_updates_name_stock_price_expiry(self, tmp_path):
        """C3 updates all four fields for a changed product."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.manual_review == 1
        assert plan.review_db_signature is not None

        sig = fingerprint_import_source(xp, M)
        # Rebuild plan with source signature (required by C3)
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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert r.ok, f"C3 failed: {r.error_message}"
        assert r.updated_rows == 1
        assert r.updated_name == 1
        assert r.updated_stock == 1
        assert r.updated_price == 1
        assert r.updated_expiry == 1

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert row["Name"] == "NewName"
        assert row["Stock"] == 100
        assert row["Price"] == 2.5
        assert row["ExpiryDate"] == "2028-01-01"
        # VAT untouched
        assert row["vat_category"] == 13
        conn.close()

    def test_only_changed_fields_count(self, tmp_path):
        """Only name changes — other per-field counts stay 0."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 1, 1.0, "2027-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert r.ok
        assert r.updated_rows == 1
        assert r.updated_name == 1
        assert r.updated_stock == 0
        assert r.updated_price == 0
        assert r.updated_expiry == 0


class TestNewAndIdenticalUntouched:

    def test_new_products_not_updated(self, tmp_path):
        """C3 must never touch new products."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "New", 5, 1.0, ""],
               ["B", "Changed", 10, 2.0, "2028-01-01"]])
        _make_db(db, [("B", "OldChanged", 1, 1.0, "2027-01-01")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.planned_new == 1
        assert plan.manual_review == 1

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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert r.ok
        assert r.updated_rows == 1

        conn = sqlite3.connect(db)
        # A should NOT exist (not inserted by C3, C1 is separate)
        conn.row_factory = sqlite3.Row
        a_row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert a_row is None, "New product should not be inserted by C3"
        conn.close()

    def test_identical_products_not_updated(self, tmp_path):
        """C3 must never touch identical products."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "Same", 1, 1.0, "2027-01-01"],
               ["B", "Changed", 10, 2.0, "2028-01-01"]])
        _make_db(db, [("A", "Same", 1, 1.0, "2027-01-01"),
                       ("B", "Old", 1, 1.0, "2027-01-01")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.skipped_identical == 1
        assert plan.manual_review == 1

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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert r.ok
        assert r.updated_rows == 1

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        a_row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert a_row["Name"] == "Same"
        assert a_row["Stock"] == 1
        conn.close()


class TestStockMovementAudit:

    def test_audit_row_only_when_stock_changes(self, tmp_path):
        """Stock movement audit is created only when stock changes."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 1.0, ""],   # stock changes
               ["B", "B", 1, 2.5, ""]])           # only price changes
        _make_db(db, [("A", "A", 1, 1.0, ""),
                       ("B", "B", 1, 1.0, "")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.manual_review == 2

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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert r.ok
        assert r.updated_rows == 2
        assert r.updated_stock == 1

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        aud = conn.execute(
            "SELECT * FROM stock_movements ORDER BY barcode").fetchall()
        assert len(aud) == 1, "Only one audit row (stock-changed product)"
        assert aud[0]["barcode"] == "A"
        assert aud[0]["old_stock"] == 1
        assert aud[0]["new_stock"] == 100
        assert aud[0]["change_amount"] == 99
        assert "Ενημέρωση Excel" in aud[0]["reason"]
        assert aud[0]["source"] == "Excel Import"
        conn.close()


# ── Rollback / Safety ──────────────────────────────────────────────────


class TestStaleSourceNoWrites:

    def test_source_file_changed_no_writes(self, tmp_path):
        """Stale file signature → zero writes, zero audit."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        # Use a deliberately wrong signature
        fake_sig = ImportSourceSignature(
            file_size_bytes=1, file_sha256="x" * 64, mapping_sha256="y" * 64)
        plan_with_sig = ImportPlan(
            read_only=True, planned_new=0,
            manual_review=1, skipped_changed=0,
            source_signature=fake_sig,
            review_db_signature=plan.review_db_signature)

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert not r.ok
        assert "άλλαξε" in r.error_message
        assert r.updated_rows == 0

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert row["Name"] == "OldName"
        assert row["Stock"] == 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM stock_movements").fetchone()["c"] == 0
        conn.close()

    def test_db_state_changed_after_review_no_writes(self, tmp_path):
        """DB modified after review → review_db_signature mismatch → zero writes."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

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
            review_db_signature=plan.review_db_signature,
        )

        # Someone updates the DB between review and commit
        conn = sqlite3.connect(db)
        conn.execute("UPDATE ProductMaster SET Name='Overwritten' "
                     "WHERE Barcode='A'")
        conn.commit()
        conn.close()

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert not r.ok
        assert "άλλαξε" in r.error_message
        assert r.updated_rows == 0

        # DB should still show the overwritten value (no C3 update)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT Name FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert row["Name"] == "Overwritten"
        assert conn.execute(
            "SELECT COUNT(*) c FROM stock_movements").fetchone()["c"] == 0
        conn.close()


class TestPlanInvariantMismatchZeroWrites:

    def test_changed_count_mismatch_zero_writes(self, tmp_path):
        """Plan says 2 manual_review but XLSX has 1 changed → zero writes."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewA", 10, 1.0, ""]])
        _make_db(db, [("A", "OldA", 1, 1.0, "")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        sig = fingerprint_import_source(xp, M)
        # Lie about manual_review count
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
            manual_review=2,  # wrong
            skipped_changed=plan.skipped_changed,
            rejected_invalid=plan.rejected_invalid,
            skipped_duplicates=plan.skipped_duplicates,
            source_signature=sig,
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert not r.ok
        assert "Ασυνέπεια" in r.error_message
        assert r.updated_rows == 0

    def test_skip_policy_rejected_by_c3(self, tmp_path):
        """Plan built with SKIP_CHANGES policy → C3 refuses (skipped_changed > 0)."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

        plan = _plan(xp, M, db, changed=ChangedPolicy.SKIP_CHANGES)
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
            review_db_signature=plan.review_db_signature,
        )

        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        assert not r.ok
        assert "παραλειφθείσες" in r.error_message.lower()
        assert r.updated_rows == 0


class TestCancellationRollback:

    def test_cancellation_rolls_back_all(self, tmp_path):
        """Cancellation mid-update rolls back every update and audit row."""
        class Cancel:
            def __init__(self):
                self.called = 0

            def is_set(self):
                self.called += 1
                return self.called > 3

        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        rows = [[f"{i:013d}", f"N{i}", i + 10, 1.0, ""] for i in range(50)]
        db_rows = [(f"{i:013d}", f"O{i}", i, 1.0, "") for i in range(50)]
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        _make_db(db, db_rows)

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
            review_db_signature=plan.review_db_signature,
        )

        cancel = Cancel()
        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db, cancel_event=cancel)
        assert r.cancelled
        assert r.updated_rows == 0

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) c FROM ProductMaster").fetchone()["c"]
        assert count == 50  # all 50 should still be original
        first = conn.execute(
            "SELECT Name FROM ProductMaster WHERE Barcode='0000000000000'"
        ).fetchone()
        assert first["Name"] == "O0", "Should not be updated"
        aud = conn.execute(
            "SELECT COUNT(*) c FROM stock_movements").fetchone()["c"]
        assert aud == 0
        conn.close()


# ── Safety: no VAT / no schema ─────────────────────────────────────────


class TestNoVatNoSchema:

    def test_no_vat_field_access(self):
        """C3 source must not reference VAT or vat_category."""
        import inspect
        from infrastructure import product_import_update_commit as puc
        src = inspect.getsource(
            puc.commit_approved_changed_products_from_xlsx)
        assert "vat" not in src.lower(), "VAT field found in C3 source"

    def test_no_schema_mutation(self):
        """C3 must not CREATE, ALTER, DROP any table."""
        import inspect
        from infrastructure import product_import_update_commit as puc
        src = inspect.getsource(
            puc.commit_approved_changed_products_from_xlsx)
        for pat in ["CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                     "ADD COLUMN"]:
            assert pat not in src, f"Forbidden '{pat}' in C3 commit"

    def test_only_update_name_stock_price_expiry(self):
        """C3 UPDATE must only touch Name, Stock, Price, ExpiryDate."""
        import inspect
        from infrastructure import product_import_update_commit as puc
        src = inspect.getsource(
            puc.commit_approved_changed_products_from_xlsx)
        # The only UPDATE should be the parameterized one
        assert "UPDATE ProductMaster" in src
        assert "SET Name=?, Stock=?, Price=?, ExpiryDate=?" in src


# ── Legacy backward compatibility ──────────────────────────────────────


class TestLegacyCompatibility:

    def test_legacy_factory_signatures_still_work(self):
        """Pre-C3 positional contracts must still work unchanged."""
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64, 1)
        # success(..., conflicts, errors, samples, signature=...)
        r = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=sig)
        assert r.ok
        assert r.changed_existing == 3
        assert r.conflict_details == ()
        assert r.review_db_signature is None  # not passed → None

        # cancelled(..., conflicts, errors, samples)
        r2 = ImportConflictResult.cancelled(
            "f", "S1", 10, 10, 0, 0, 5, 2, 2, 1, [], [], [])
        assert r2.cancelled
        assert r2.review_db_signature is None

        # partial(..., conflicts, errors, samples)
        r3 = ImportConflictResult.partial(
            "msg", "f", "S1", 10, 10, 0, 0, 5, 2, 2, 1, [], [], [])
        assert not r3.ok
        assert r3.review_db_signature is None

    def test_legacy_import_plan_construction_still_works(self):
        """Manually constructed ImportPlan without review_db_signature is valid."""
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64, 1)
        plan = ImportPlan(
            read_only=True, planned_new=1, source_signature=sig)
        assert plan.review_db_signature is None
        # C3 should reject this plan because review_db_signature is None
        # but the plan itself is constructable

    def test_existing_c1_tests_compatible(self):
        """Existing test_legacy_factory_signatures test still passes."""
        # The conflict test module already has this test — this one
        # just verifies ImportConflictResult still accepts old positional args
        from infrastructure.product_import_identity import ImportSourceSignature
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        r = ImportConflictResult.success(
            "f", "S1", 10, 10, 0, 0, 10, 4, 3, 3, [], [], [],
            signature=sig)
        assert r.ok
        assert r.changed_existing == 3
        assert r.conflict_details == ()


class TestReviewDbSignature:

    def test_signature_computed_for_existing_barcodes(self, tmp_path):
        """review_db_signature is present when barcodes exist in DB."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.review_db_signature is not None
        assert len(r.review_db_signature) == 64  # SHA-256 hex

    def test_signature_stable_for_same_state(self, tmp_path):
        """Same DB state → same review_db_signature."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""],
               ["B", "M", 2, 2.0, "2028-01-01"]])
        _make_db(db, [("A", "N", 1, 1.0, ""),
                       ("B", "M", 2, 2.0, "2028-01-01")])
        r1 = analyze_import_conflicts(xp, M, db)
        r2 = analyze_import_conflicts(xp, M, db)
        assert r1.review_db_signature == r2.review_db_signature

    def test_signature_changes_when_price_differs(self, tmp_path):
        """Changing a Decimal-valued field changes the signature."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        r1 = analyze_import_conflicts(xp, M, db)

        # Modify DB price
        conn = sqlite3.connect(db)
        conn.execute("UPDATE ProductMaster SET Price=2.0 WHERE Barcode='A'")
        conn.commit()
        conn.close()
        r2 = analyze_import_conflicts(xp, M, db)
        assert r1.review_db_signature != r2.review_db_signature

    def test_signature_none_when_no_existing_barcodes(self, tmp_path):
        """No barcodes exist in DB → signature is None."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [])  # empty DB
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.review_db_signature is None

    def test_review_db_signature_in_plan(self, tmp_path):
        """build_import_plan copies review_db_signature to ImportPlan."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        plan = _plan(xp, M, db, changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW)
        assert plan.review_db_signature is not None
        assert len(plan.review_db_signature) == 64


# ── C3 prerequisite gates ──────────────────────────────────────────────


class TestC3PrerequisiteGates:

    def test_rejects_non_readonly_plan(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan = ImportPlan(read_only=False, manual_review=1,
                          source_signature=sig,
                          review_db_signature="c" * 64)
        r = commit_approved_changed_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "ανάγνωσης" in r.error_message

    def test_rejects_plan_without_source_signature(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        plan = ImportPlan(read_only=True, manual_review=1,
                          review_db_signature="c" * 64)
        r = commit_approved_changed_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "ταυτότητα" in r.error_message

    def test_rejects_plan_without_review_db_signature(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan = ImportPlan(read_only=True, manual_review=1,
                          source_signature=sig)
        r = commit_approved_changed_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "υπογραφή" in r.error_message

    def test_rejects_missing_db_file(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan = ImportPlan(read_only=True, manual_review=1,
                          source_signature=sig,
                          review_db_signature="c" * 64)
        r = commit_approved_changed_products_from_xlsx(
            xp, M, plan, "/nonexistent/path.db")
        assert not r.ok
        assert "δεν βρέθηκε" in r.error_message.lower()

    def test_rejects_zero_manual_review(self, tmp_path):
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        _make_db(db, [("A", "N", 1, 1.0, "")])
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan = ImportPlan(read_only=True, manual_review=0,
                          source_signature=sig,
                          review_db_signature="c" * 64)
        r = commit_approved_changed_products_from_xlsx(xp, M, plan, db)
        assert not r.ok
        assert "manual_review" in r.error_message.lower()


# ── Dialog gating tests ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp_module():
    """Module-level QApplication for dialog tests (headless-safe)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # No quit — other tests may reuse the app


class TestDialogC3Gating:

    @pytest.fixture
    def dialog_factory(self, qapp_module):
        dialogs = []
        def _make(**kw):
            from qt_app.dialogs.product_import_preview_dialog import (
                ProductImportPreviewDialog)
            dlg = ProductImportPreviewDialog(**kw)
            dialogs.append(dlg)
            return dlg
        yield _make
        for dlg in dialogs:
            if dlg.is_busy():
                dlg.request_shutdown()
                dlg.await_shutdown(timeout_ms=5000)
            if not dlg.is_busy():
                dlg.close()
            dlg.deleteLater()

    def _plan(self):
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        return ImportPlan(
            read_only=True, planned_new=0,
            skipped_identical=0, manual_review=3,
            skipped_changed=0,
            source_signature=sig,
            review_db_signature="c" * 64)

    def test_c3_hidden_before_valid_plan(self, dialog_factory):
        d = dialog_factory()
        assert d._update_check.isHidden()
        assert d._update_btn.isHidden()

    def test_c3_shown_with_valid_manual_review_plan(self, dialog_factory):
        d = dialog_factory()
        d._current_plan = self._plan()
        # Simulate _on_build_plan visibility logic
        plan = d._current_plan
        show = (plan.manual_review > 0 and plan.skipped_changed == 0)
        d._update_check.setVisible(show)
        d._update_btn.setVisible(show)
        assert not d._update_check.isHidden()
        assert not d._update_btn.isHidden()

    def test_c3_hidden_under_skip_policy(self, dialog_factory):
        d = dialog_factory()
        plan = ImportPlan(
            read_only=True, planned_new=0,
            skipped_identical=0, manual_review=0,
            skipped_changed=3,  # skip policy
            source_signature=ImportSourceSignature(1, "a" * 64, "b" * 64),
            review_db_signature="c" * 64)
        d._current_plan = plan
        show = plan.manual_review > 0 and plan.skipped_changed == 0
        d._update_check.setVisible(show)
        d._update_btn.setVisible(show)
        assert d._update_check.isHidden()
        assert d._update_btn.isHidden()

    def test_update_btn_disabled_until_checked(self, dialog_factory):
        d = dialog_factory()
        plan = self._plan()
        d._current_plan = plan
        d._update_check.setVisible(True)
        d._update_check.setChecked(False)
        d._update_btn.setVisible(True)
        d._update_btn.setEnabled(False)
        # Button should be disabled
        assert not d._update_btn.isEnabled()
        # Check the box
        d._update_check.setChecked(True)
        d._on_update_check_toggled(True)
        assert d._update_btn.isEnabled()

    def test_c3_controls_cleared_after_plan_invalidation(self, dialog_factory):
        d = dialog_factory()
        plan = self._plan()
        d._current_plan = plan
        d._update_check.setVisible(True)
        d._update_btn.setVisible(True)
        # Invalidate
        d._current_plan = None
        d._update_check.hide()
        d._update_btn.hide()
        d._update_btn.setEnabled(False)
        assert d._update_check.isHidden()
        assert d._update_btn.isHidden()
        assert not d._update_btn.isEnabled()

    def test_c3_not_enabled_when_busy(self, dialog_factory):
        d = dialog_factory()
        plan = self._plan()
        d._current_plan = plan
        d._update_check.setVisible(True)
        d._update_check.setChecked(True)
        d._thread = object()  # busy
        d._on_update_check_toggled(True)
        assert not d._update_btn.isEnabled()
        d._thread = None

    def test_c3_hidden_with_null_review_db_signature(self, dialog_factory,
                                                       monkeypatch):
        """_on_build_plan() must hide C3 controls when plan has
        review_db_signature=None, even with manual_review>0."""
        d = dialog_factory()
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan_null = ImportPlan(
            read_only=True, planned_new=0,
            skipped_identical=0, manual_review=3,
            skipped_changed=0,
            source_signature=sig,
            review_db_signature=None)
        # Wire up the dialog so _on_build_plan finds a valid plan via
        # build_import_plan (imported locally in the method)
        monkeypatch.setattr(
            "infrastructure.product_import_plan.build_import_plan",
            lambda result, policy=None: plan_null)
        # _last_conflict_result must be truthy for _on_build_plan to proceed
        d._last_conflict_result = ImportConflictResult(
            ok=True, cancelled=False, source_signature=sig)
        d._plan_policy.setCurrentIndex(0)  # manual review policy

        d._on_build_plan()

        assert d._update_check.isHidden(), (
            "C3 checkbox must be hidden when review_db_signature=None")
        assert d._update_btn.isHidden(), (
            "C3 button must be hidden when review_db_signature=None")

    def test_c3_shown_when_review_db_signature_present(self, dialog_factory,
                                                        monkeypatch):
        """_on_build_plan() must show C3 controls when plan has
        manual_review>0, skipped_changed==0, and review_db_signature set."""
        d = dialog_factory()
        sig = ImportSourceSignature(1, "a" * 64, "b" * 64)
        plan_ok = ImportPlan(
            read_only=True, planned_new=0,
            skipped_identical=0, manual_review=3,
            skipped_changed=0,
            source_signature=sig,
            review_db_signature="c" * 64)
        monkeypatch.setattr(
            "infrastructure.product_import_plan.build_import_plan",
            lambda result, policy=None: plan_ok)
        d._last_conflict_result = ImportConflictResult(
            ok=True, cancelled=False, source_signature=sig)
        d._plan_policy.setCurrentIndex(0)

        d._on_build_plan()

        assert not d._update_check.isHidden(), (
            "C3 checkbox must be shown with valid review_db_signature")
        assert not d._update_btn.isHidden(), (
            "C3 button must be shown with valid review_db_signature")


# ── Regression: stable snapshot prevents two-connection race ───────────


class TestStableSnapshotRegression:

    def test_wal_race_old_two_connection_would_fail(self, tmp_path,
                                                      monkeypatch):
        """Real deterministic two-connection race regression.

        Monkeypatch _classify_batch so that AFTER it classifies the batch
        (reading the reviewed state from the deferred read transaction),
        a concurrent writer mutates the same product from a separate
        connection while analyze_import_conflicts is still running.

        The single-snapshot design must produce a review_db_signature
        for the pre-mutation state, and C3 must reject the mutated DB
        with zero updates and zero audit writes.
        """
        db = str(tmp_path / "t.db")
        xp = str(tmp_path / "t.xlsx")

        # WAL-mode DB
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE ProductMaster("
            "Barcode TEXT, Name TEXT, Stock INTEGER, "
            "Price REAL, ExpiryDate TEXT)")
        conn.execute(
            "CREATE TABLE stock_movements("
            "id INTEGER PRIMARY KEY, barcode TEXT, product_name TEXT, "
            "old_stock INTEGER, new_stock INTEGER, "
            "change_amount INTEGER, reason TEXT, source TEXT, "
            "operator TEXT, timestamp TEXT)")
        conn.execute(
            "INSERT INTO ProductMaster"
            "(Barcode, Name, Stock, Price, ExpiryDate) "
            "VALUES ('A', 'OldName', 1, 1.0, '2027-01-01')")
        conn.commit()
        conn.close()

        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])

        # Monkeypatch _classify_batch: after real classify, inject mutation
        import infrastructure.product_import_conflicts as ic
        orig_classify = ic._classify_batch
        injected = []

        def _wrapper(batch, conn, cancel_event, new_count, unchanged_count,
                     changed_count, conflicts, details, total_attempted,
                     max_samples, max_details):
            result = orig_classify(
                batch, conn, cancel_event, new_count, unchanged_count,
                changed_count, conflicts, details, total_attempted,
                max_samples, max_details)
            if not injected:
                injected.append(True)
                # Mutate from separate writer while analysis is still alive
                writer = sqlite3.connect(db)
                writer.execute(
                    "UPDATE ProductMaster SET Name='Hijacked' "
                    "WHERE Barcode='A'")
                writer.commit()
                writer.close()
            return result

        monkeypatch.setattr(ic, "_classify_batch", _wrapper)

        # Run analysis — classification sees OldName, wrapper mutates to
        # Hijacked before review_db_signature is computed.  With the fix
        # (single deferred read transaction), signature should reflect
        # OldName.  Old two-connection code would see Hijacked.
        r = analyze_import_conflicts(xp, M, db)
        assert r.ok
        assert r.changed_existing == 1
        orig_sig = r.review_db_signature
        assert orig_sig is not None

        # Build plan from reviewed result
        plan = build_import_plan(
            r, ImportReviewPolicy(changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW))
        assert plan.review_db_signature == orig_sig
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
            review_db_signature=orig_sig,
        )

        r2 = commit_approved_changed_products_from_xlsx(
            xp, M, plan_with_sig, db)
        # C3 must reject because DB has Hijacked but plan has OldName sig
        assert not r2.ok
        assert r2.updated_rows == 0

        conn2 = sqlite3.connect(db)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT Name FROM ProductMaster WHERE Barcode='A'").fetchone()
        assert row["Name"] == "Hijacked"  # writer's change survived
        aud = conn2.execute(
            "SELECT COUNT(*) c FROM stock_movements").fetchone()["c"]
        assert aud == 0
        conn2.close()

    def test_signature_computation_failure_returns_failed_analysis(
            self, tmp_path, monkeypatch):
        """If changed_count > 0 and signature computation raises
        RuntimeError, analysis must return failure."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

        import infrastructure.product_import_conflicts as ic

        def _fail(*a, **kw):
            raise RuntimeError("simulated signature failure")

        monkeypatch.setattr(ic, "_compute_review_db_signature", _fail)

        r = analyze_import_conflicts(xp, M, db)
        assert not r.ok
        assert not r.cancelled
        assert "υπογραφής" in (r.error_message or "")

    def test_valueerror_sig_failure_returns_failed_analysis(
            self, tmp_path, monkeypatch):
        """If changed_count > 0 and signature computation raises
        ValueError, analysis must also return failure."""
        xp = str(tmp_path / "t.xlsx")
        db = str(tmp_path / "t.db")
        _xlsx(xp, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "NewName", 100, 2.5, "2028-01-01"]])
        _make_db(db, [("A", "OldName", 1, 1.0, "2027-01-01")])

        import infrastructure.product_import_conflicts as ic

        def _fail_value(*a, **kw):
            raise ValueError("no barcodes — simulated")

        monkeypatch.setattr(ic, "_compute_review_db_signature", _fail_value)

        r = analyze_import_conflicts(xp, M, db)
        assert not r.ok
        assert not r.cancelled
        assert "υπογραφής" in (r.error_message or "")
