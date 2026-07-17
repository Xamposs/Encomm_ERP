"""
Read-only import plan builder (Phase B3).  Pure logic — no SQLite, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from infrastructure.product_import_conflicts import ImportConflictResult
from infrastructure.product_import_identity import ImportSourceSignature


class ChangedPolicy(Enum):
    REQUIRE_MANUAL_REVIEW = "review"
    SKIP_CHANGES = "skip"


@dataclass(frozen=True)
class ImportReviewPolicy:
    changed: ChangedPolicy = ChangedPolicy.REQUIRE_MANUAL_REVIEW


@dataclass(frozen=True)
class ImportPlan:
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
    skipped_changed: int = 0
    rejected_invalid: int = 0
    skipped_duplicates: int = 0
    source_signature: ImportSourceSignature | None = None
    # ── C3 fields (appended; never inserted between legacy fields) ───
    review_db_signature: str | None = None


def build_import_plan(
    result: ImportConflictResult,
    policy: ImportReviewPolicy = ImportReviewPolicy(),
) -> ImportPlan:
    if result.cancelled:
        raise ValueError(
            "Δεν μπορεί να δημιουργηθεί σχέδιο από ακυρωμένη ανάλυση.")
    if not result.ok:
        raise ValueError(
            f"Αδυναμία δημιουργίας σχεδίου: "
            f"{result.error_message or 'Η ανάλυση απέτυχε.'}")
    if result.source_signature is None:
        raise ValueError(
            "Απαιτείται επαληθευμένη ταυτότητα αρχείου για το σχέδιο.")

    if policy.changed == ChangedPolicy.REQUIRE_MANUAL_REVIEW:
        manual_review = result.changed_existing
        skipped_changed = 0
    else:
        manual_review = 0
        skipped_changed = result.changed_existing

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
        manual_review=manual_review,
        skipped_changed=skipped_changed,
        rejected_invalid=result.invalid_rows,
        skipped_duplicates=result.duplicate_barcodes,
        source_signature=result.source_signature,
        review_db_signature=result.review_db_signature,
    )

    total = (plan.planned_new + plan.skipped_identical
             + plan.manual_review + plan.skipped_changed)
    if total != plan.classified_rows:
        raise ValueError(
            f"Ασυνέπεια σχεδίου: ταξινομήθηκαν {plan.classified_rows} "
            f"αλλά σχεδιάστηκαν {total}.")

    return plan
