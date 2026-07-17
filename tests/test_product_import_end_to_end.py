"""End-to-end XLSX product import workflow verification (Phase C4)."""

import sqlite3, pytest, openpyxl
from infrastructure.product_import_preview import ImportColumnMapping
from infrastructure.product_import_conflicts import analyze_import_conflicts
from infrastructure.product_import_plan import (
    build_import_plan, ImportReviewPolicy, ChangedPolicy, ImportPlan)
from infrastructure.product_import_identity import fingerprint_import_source
from infrastructure.product_import_commit import commit_new_products_from_xlsx
from infrastructure.product_import_update_commit import (
    commit_approved_changed_products_from_xlsx)


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


def _run_analysis(xp, mapping, db_path):
    """Run B1 conflict analysis and build a manual-review plan.
    Returns (result, plan)."""
    r = analyze_import_conflicts(xp, mapping, db_path)
    assert r.ok, f"B1 analysis failed: {r.error_message}"
    plan = build_import_plan(
        r, ImportReviewPolicy(changed=ChangedPolicy.REQUIRE_MANUAL_REVIEW))
    return r, plan


def _run_c1(xp, mapping, plan, db_path):
    """Execute C1 commit. Returns the commit result."""
    sig = fingerprint_import_source(xp, mapping)
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
    r = commit_new_products_from_xlsx(xp, mapping, plan_with_sig, db_path)
    return r


def _run_c3(xp, mapping, plan, db_path):
    """Execute C3 update commit. Returns the update result."""
    sig = fingerprint_import_source(xp, mapping)
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
        xp, mapping, plan_with_sig, db_path)
    return r


class TestEndToEnd:

    def test_full_c1_c3_workflow(self, tmp_path):
        """Complete E2E: seed DB → C1 inserts new → C3 updates changed →
        final analysis confirms all identical, no remaining changes."""
        db = str(tmp_path / "t.db")
        xp = str(tmp_path / "t.xlsx")
        HEADERS = ["Barcode", "Name", "Stock", "Price", "Expiry"]

        # ── Seed: one product that will change, one that is identical ─
        _make_db(db, [
            ("CHG", "OldChanged", 1, 1.0, "2027-01-01"),
            ("SAME", "SameProduct", 5, 5.0, "2028-06-01"),
        ])

        # ── XLSX: one new, one changed, one identical ────────────────
        _xlsx(xp, HEADERS, [
            ["NEW", "NewProduct", 10, 10.0, "2029-12-31"],
            ["CHG", "NewChanged", 100, 2.5, "2028-12-31"],
            ["SAME", "SameProduct", 5, 5.0, "2028-06-01"],
        ])

        # ═══════════════════════════════════════════════════════════════
        # Step 1: First analysis + plan
        # ═══════════════════════════════════════════════════════════════
        r1, plan1 = _run_analysis(xp, M, db)
        assert r1.valid_rows == 3
        assert r1.new_barcodes == 1
        assert r1.unchanged_existing == 1
        assert r1.changed_existing == 1
        assert r1.source_signature is not None
        assert r1.review_db_signature is not None
        assert plan1.planned_new == 1
        assert plan1.skipped_identical == 1
        assert plan1.manual_review == 1
        assert plan1.skipped_changed == 0

        # ═══════════════════════════════════════════════════════════════
        # Step 2: C1 — insert new product only
        # ═══════════════════════════════════════════════════════════════
        c1_result = _run_c1(xp, M, plan1, db)
        assert c1_result.ok, f"C1 failed: {c1_result.error_message}"
        assert c1_result.inserted_rows == 1

        # Verify DB state after C1
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        # New product inserted
        new_row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='NEW'").fetchone()
        assert new_row is not None
        assert new_row["Name"] == "NewProduct"
        assert new_row["Stock"] == 10
        assert new_row["Price"] == 10.0
        assert new_row["ExpiryDate"] == "2029-12-31"

        # Changed existing product still unchanged by C1
        chg_row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='CHG'").fetchone()
        assert chg_row["Name"] == "OldChanged"
        assert chg_row["Stock"] == 1
        assert chg_row["Price"] == 1.0
        assert chg_row["ExpiryDate"] == "2027-01-01"

        # Identical product unchanged
        same_row = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='SAME'").fetchone()
        assert same_row["Name"] == "SameProduct"
        assert same_row["Stock"] == 5
        assert same_row["Price"] == 5.0

        # Audit: exactly one C1 stock-movement row (new product)
        aud_rows = conn.execute(
            "SELECT * FROM stock_movements ORDER BY barcode").fetchall()
        assert len(aud_rows) == 1
        assert aud_rows[0]["barcode"] == "NEW"
        assert aud_rows[0]["old_stock"] == 0
        assert aud_rows[0]["new_stock"] == 10
        assert aud_rows[0]["change_amount"] == 10
        assert "Εισαγωγή Excel" in aud_rows[0]["reason"]
        conn.close()

        # ═══════════════════════════════════════════════════════════════
        # Step 3: Fresh analysis on same XLSX (after C1 inserted NEW)
        # ═══════════════════════════════════════════════════════════════
        r2, plan2 = _run_analysis(xp, M, db)
        assert r2.valid_rows == 3
        # NEW is now identical to what C1 inserted
        assert r2.new_barcodes == 0, (
            "NEW should now be identical, not new")
        assert r2.unchanged_existing == 2, (
            f"Expected 2 identical (NEW + SAME), got {r2.unchanged_existing}")
        assert r2.changed_existing == 1, (
            "CHG should still require manual review")
        assert r2.source_signature is not None
        assert r2.review_db_signature is not None
        assert plan2.planned_new == 0
        assert plan2.skipped_identical == 2
        assert plan2.manual_review == 1

        # ═══════════════════════════════════════════════════════════════
        # Step 4: C3 — update changed existing product
        # ═══════════════════════════════════════════════════════════════
        c3_result = _run_c3(xp, M, plan2, db)
        assert c3_result.ok, f"C3 failed: {c3_result.error_message}"
        assert c3_result.updated_rows == 1
        assert c3_result.updated_name == 1
        assert c3_result.updated_stock == 1
        assert c3_result.updated_price == 1
        assert c3_result.updated_expiry == 1

        # Verify DB state after C3
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        # CHG updated with incoming values
        chg_row2 = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='CHG'").fetchone()
        assert chg_row2["Name"] == "NewChanged"
        assert chg_row2["Stock"] == 100
        assert chg_row2["Price"] == 2.5
        assert chg_row2["ExpiryDate"] == "2028-12-31"

        # NEW still unchanged (inserted by C1)
        new_row2 = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='NEW'").fetchone()
        assert new_row2["Name"] == "NewProduct"
        assert new_row2["Stock"] == 10
        assert new_row2["Price"] == 10.0

        # SAME still unchanged
        same_row2 = conn.execute(
            "SELECT * FROM ProductMaster WHERE Barcode='SAME'").fetchone()
        assert same_row2["Name"] == "SameProduct"
        assert same_row2["Stock"] == 5
        assert same_row2["Price"] == 5.0

        # Audit: now 2 rows (C1 + C3)
        aud_rows2 = conn.execute(
            "SELECT * FROM stock_movements ORDER BY barcode").fetchall()
        assert len(aud_rows2) == 2
        # C3 audit row for CHG
        chg_aud = [a for a in aud_rows2 if a["barcode"] == "CHG"][0]
        assert chg_aud["old_stock"] == 1
        assert chg_aud["new_stock"] == 100
        assert chg_aud["change_amount"] == 99
        assert "Ενημέρωση Excel" in chg_aud["reason"]
        conn.close()

        # ═══════════════════════════════════════════════════════════════
        # Step 5: Final fresh analysis — all identical
        # ═══════════════════════════════════════════════════════════════
        r3, plan3 = _run_analysis(xp, M, db)
        assert r3.valid_rows == 3
        assert r3.new_barcodes == 0, "No new products should remain"
        assert r3.changed_existing == 0, (
            "No changed products should remain")
        assert r3.unchanged_existing == 3, (
            "All three should be identical")
        assert plan3.planned_new == 0
        assert plan3.manual_review == 0
        assert plan3.skipped_identical == 3

        # Accounting invariants
        assert r3.classified_rows == r3.valid_rows
        assert r3.classified_rows == (
            r3.new_barcodes + r3.unchanged_existing + r3.changed_existing)
        assert plan3.classified_rows == (
            plan3.planned_new + plan3.skipped_identical
            + plan3.manual_review + plan3.skipped_changed)
