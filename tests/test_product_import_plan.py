"""Unit tests for ImportPlan and build_import_plan()."""

import pytest
from infrastructure.product_import_plan import (
    build_import_plan, ImportReviewPolicy, ImportPlan, ChangedPolicy)
from infrastructure.product_import_conflicts import ImportConflictResult


def _ok_result(**kw):
    from infrastructure.product_import_identity import ImportSourceSignature
    defaults = dict(
        ok=True, cancelled=False, file_name="f", sheet_name="S",
        scanned_rows=10, valid_rows=10, invalid_rows=0,
        duplicate_barcodes=0, classified_rows=10,
        new_barcodes=4, unchanged_existing=3, changed_existing=3,
        conflict_samples=(), errors=(), sample_rows=(),
        source_signature=ImportSourceSignature(
            file_size_bytes=100, file_sha256="a"*64, mapping_sha256="b"*64))
    defaults.update(kw)
    return ImportConflictResult(**defaults)


class TestBuildPlan:

    def test_cancelled_raises(self):
        r = _ok_result(cancelled=True, ok=False)
        with pytest.raises(ValueError, match="ακυρωμένη"):
            build_import_plan(r)

    def test_failed_raises(self):
        r = _ok_result(ok=False, error_message="test fail")
        with pytest.raises(ValueError, match="test fail"):
            build_import_plan(r)

    def test_default_policy_manual_review(self):
        r = _ok_result()
        plan = build_import_plan(r)
        assert plan.manual_review == 3
        assert plan.skipped_changed == 0

    def test_skip_changes_policy(self):
        r = _ok_result()
        plan = build_import_plan(
            r, ImportReviewPolicy(changed=ChangedPolicy.SKIP_CHANGES))
        assert plan.manual_review == 0
        assert plan.skipped_changed == 3

    def test_accounting_invariant_review(self):
        r = _ok_result()
        plan = build_import_plan(r)
        total = (plan.planned_new + plan.skipped_identical
                 + plan.manual_review + plan.skipped_changed)
        assert total == plan.classified_rows

    def test_accounting_invariant_skip(self):
        r = _ok_result()
        plan = build_import_plan(
            r, ImportReviewPolicy(changed=ChangedPolicy.SKIP_CHANGES))
        total = (plan.planned_new + plan.skipped_identical
                 + plan.manual_review + plan.skipped_changed)
        assert total == plan.classified_rows

    def test_frozen_immutable(self):
        r = _ok_result()
        plan = build_import_plan(r)
        with pytest.raises(Exception):
            plan.planned_new = 999

    def test_rejects_missing_signature(self):
        r = _ok_result(source_signature=None)
        with pytest.raises(ValueError, match="ταυτότητα"):
            build_import_plan(r)

    def test_no_write_sql(self):
        import inspect
        from infrastructure import product_import_plan as pip
        src = inspect.getsource(pip.build_import_plan)
        for pat in ["INSERT", "UPDATE", "DELETE", "DROP",
                     "ALTER", "CREATE", "REPLACE", "sqlite3"]:
            assert pat not in src, f"Forbidden '{pat}' in plan module"
