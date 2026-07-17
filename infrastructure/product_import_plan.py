"""
Read-only import plan builder (Phase B3).  Pure logic — no SQLite, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from infrastructure.product_import_conflicts import ImportConflictResult


class ChangedPolicy(Enum):
    REQUIRE_MANUAL_REVIEW = "review"
    SKIP_CHANGES = "skip"


@dataclass(frozen=True)
class ImportReviewPolicy:
    changed: ChangedPolicy = ChangedPolicy.REQUIRE_MANUAL_REVIEW


@dataclass(frozen=True)
class ImportPlan:
    """Bounded, immutable plan — retains no product data."""
    read_only: bool = True

    file_name: str = ""
    sheet_name: str = ""

    valid_rows: int = 0
    invalid_rows: int = 0
    duplicate_barcodes: int = 0
    classified_rows: int = 0

    planned_new: int = 0
    skipped_identical: int = 0
    manual_review: int = 0
    rejected_invalid: int = 0
    skipped_duplicates: int = 0


def build_import_plan(
    result: ImportConflictResult,
    policy: ImportReviewPolicy = ImportReviewPolicy(),
) -> ImportPlan:
    """Build a read-only import plan from a conflict analysis result.

    Raises ValueError for cancelled, failed, or invalid results.
    """
    if result.cancelled:
        raise ValueError("Δεν μπορεί να δημιουργηθεί σχέδιο από ακυρωμένη ανάλυση.")
    if not result.ok:
        raise ValueError(
            f"Αδυναμία δημιουργίας σχεδίου: {result.error_message or 'Η ανάλυση απέτυχε.'}")

    plan = ImportPlan(
        read_only=True,
        file_name=result.file_name,
        sheet_name=result.sheet_name,
        valid_rows=result.valid_rows,
        invalid_rows=result.invalid_rows,
        duplicate_barcodes=result.duplicate_barcodes,
        classified_rows=result.classified_rows,
        planned_new=result.new_barcodes,
        skipped_identical=result.unchanged_existing,
        rejected_invalid=result.invalid_rows,
        skipped_duplicates=result.duplicate_barcodes,
    )

    if policy.changed == ChangedPolicy.SKIP_CHANGES:
        object.__setattr__(plan, "manual_review", 0)
    else:
        object.__setattr__(plan, "manual_review", result.changed_existing)

    # Accounting for skip policy: changed rows were still classified,
    # they're just intentionally skipped in the plan.
    if policy.changed == ChangedPolicy.REQUIRE_MANUAL_REVIEW:
        total_classified = (plan.planned_new + plan.skipped_identical
                            + plan.manual_review)
        if total_classified != plan.classified_rows:
            raise ValueError(
                f"Ασυνέπεια σχεδίου: ταξινομήθηκαν "
                f"{plan.classified_rows} αλλά "
                f"σχεδιάστηκαν {total_classified}.")

    return plan
