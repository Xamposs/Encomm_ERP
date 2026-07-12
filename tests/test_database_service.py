"""Tests for infrastructure/database_service.py — the highest-risk layer.

Focus: atomic checkout, bulk-upsert idempotency, audit-trail correctness,
TOCTOU-free updates, and expiry data-quality detection.
"""
import sqlite3

import pytest

from core.domain_models import Product


# ── Atomic checkout ─────────────────────────────────────────────────

def _fetch_invoice(db, invoice_id):
    """Helper: return a single invoice dict by exact id, or None."""
    matches = [i for i in db.get_all_invoices(search_id=invoice_id) if i["id"] == invoice_id]
    return matches[0] if matches else None


def test_checkout_decrements_stock_and_saves_invoice(seeded_db, sample_product):
    ok, succeeded, failed, totals = seeded_db.process_checkout_transaction(
        "INV-TEST-1", [(sample_product, 5)], customer_id=None, vat_rate=0.15,
    )
    assert ok is True
    assert failed == []
    assert len(succeeded) == 1
    # Stock decremented.
    assert seeded_db.get_product(sample_product.barcode).stock == 95
    # Totals: subtotal 17.50, vat 2.62 (round(17.50*0.15,2)=2.62), grand 20.12
    assert totals["subtotal"] == 17.50
    assert totals["grand"] == round(17.50 + totals["vat"], 2)
    # Invoice persisted.
    inv = _fetch_invoice(seeded_db, "INV-TEST-1")
    assert inv is not None
    assert inv["total"] == totals["grand"]


def test_checkout_rejects_insufficient_stock_and_changes_nothing(seeded_db, sample_product):
    ok, succeeded, failed, totals = seeded_db.process_checkout_transaction(
        "INV-TEST-2", [(sample_product, 9999)], customer_id=None, vat_rate=0.15,
    )
    assert ok is False
    assert succeeded == []
    assert len(failed) == 1
    # Stock must be unchanged — nothing committed.
    assert seeded_db.get_product(sample_product.barcode).stock == 100
    # No invoice row.
    assert _fetch_invoice(seeded_db, "INV-TEST-2") is None


def test_checkout_is_atomic_partial_failure_rolls_back_all(db):
    """If one item is unavailable, the whole sale rolls back — even items
    that would have succeeded individually."""
    good = Product("5200000000017", "Good", 50, "2099-01-01", 10.0, barcode_type="EAN13")
    bad = Product("5200000000024", "Bad", 0, "2099-01-01", 10.0, barcode_type="EAN13")
    db.add_product(good)
    db.add_product(bad)

    ok, succeeded, failed, totals = db.process_checkout_transaction(
        "INV-PARTIAL", [(good, 5), (bad, 1)], customer_id=None, vat_rate=0.15,
    )
    assert ok is False
    assert succeeded == []
    # The "good" item's stock must NOT be decremented (rolled back).
    assert db.get_product(good.barcode).stock == 50
    assert _fetch_invoice(db, "INV-PARTIAL") is None


def test_checkout_writes_audit_trail(seeded_db, sample_product):
    seeded_db.process_checkout_transaction(
        "INV-AUDIT", [(sample_product, 5)], customer_id=None, vat_rate=0.15,
    )
    movements = seeded_db.get_stock_movements(barcode=sample_product.barcode)
    sale_moves = [m for m in movements if m.get("source") == "POS"]
    assert len(sale_moves) == 1
    m = sale_moves[0]
    assert m["old_stock"] == 100
    assert m["new_stock"] == 95
    assert m["change_amount"] == -5


# ── Bulk upsert idempotency ─────────────────────────────────────────

def test_bulk_upsert_is_idempotent_for_stock(db):
    """Re-importing the same price-list must NOT accumulate stock."""
    rows = [("BULK1", "Item", 20, "2099-01-01", 5.0)]
    db.bulk_upsert_products(rows)
    assert db.get_product("BULK1").stock == 20
    # Re-import the same quantity.
    db.bulk_upsert_products(rows)
    assert db.get_product("BULK1").stock == 20  # NOT 40


def test_bulk_upsert_replaces_stock_with_new_value(db):
    """When a re-import carries a *different* quantity, the new value wins."""
    db.bulk_upsert_products([("BULK2", "Item", 10, "2099-01-01", 5.0)])
    assert db.get_product("BULK2").stock == 10
    db.bulk_upsert_products([("BULK2", "Item", 7, "2099-01-01", 5.0)])
    assert db.get_product("BULK2").stock == 7


def test_bulk_upsert_audit_logs_real_old_stock(db):
    db.bulk_upsert_products([("BULK3", "Item", 10, "2099-01-01", 5.0)])
    db.bulk_upsert_products([("BULK3", "Item", 25, "2099-01-01", 5.0)])
    moves = db.get_stock_movements(barcode="BULK3")
    # Movements are returned newest-first; the most recent import must show
    # old=10 → new=25, NOT a fabricated old=0 → new=25.
    import_moves = [m for m in moves if m.get("source") == "Τιμολόγιο"]
    assert len(import_moves) >= 1
    most_recent = import_moves[0]
    assert most_recent["old_stock"] == 10
    assert most_recent["new_stock"] == 25


# ── update_product / update_stock audit correctness ─────────────────

def test_update_stock_logs_correct_old_stock(seeded_db, sample_product):
    assert seeded_db.update_stock(sample_product.barcode, 42) is True
    moves = seeded_db.get_stock_movements(barcode=sample_product.barcode)
    stock_moves = [m for m in moves if m.get("source") == "Ενημέρωση Στοκ"]
    # Movements are returned newest-first, so [0] is the most recent.
    assert stock_moves[0]["old_stock"] == 100
    assert stock_moves[0]["new_stock"] == 42


def test_update_product_logs_correct_old_stock(seeded_db, sample_product):
    sample_product.stock = 77
    assert seeded_db.update_product(sample_product) is True
    moves = seeded_db.get_stock_movements(barcode=sample_product.barcode)
    form_moves = [m for m in moves if m.get("source") == "Φόρμα Προϊόντος"]
    # Most recent first.
    assert form_moves[0]["old_stock"] == 100
    assert form_moves[0]["new_stock"] == 77


def test_stock_cannot_go_negative_on_new_db(db):
    """The CHECK (Stock >= 0) constraint must reject negative stock on a
    fresh DB (the constraint is only created for new DBs)."""
    # Direct INSERT of a negative value should violate the constraint.
    conn = sqlite3.connect(db.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
                "VALUES (?, ?, ?, ?, ?)",
                ("NEG1", "Bad", -1, "2099-01-01", 1.0),
            )
    finally:
        conn.close()


# ── Expiry data-quality detection ───────────────────────────────────

def test_get_expiry_data_issues_finds_unparseable(db):
    # Insert a product with a garbage expiry directly (bypassing validation).
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("GARB1", "Bad Date", 5, "31/12/2025", 1.0),  # non-ISO → SQLite date() = NULL
    )
    conn.execute(
        "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("OK1", "Good Date", 5, "2025-12-31", 1.0),
    )
    conn.commit()
    conn.close()

    issues = db.get_expiry_data_issues()
    barcodes = [p.barcode for p in issues]
    assert "GARB1" in barcodes
    assert "OK1" not in barcodes


def test_get_expiry_data_issues_ignores_empty(db):
    """Empty expiry is an allowed 'no expiry' sentinel, not a data issue."""
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("EMPTY1", "No Expiry", 5, "", 1.0),
    )
    conn.commit()
    conn.close()
    issues = db.get_expiry_data_issues()
    assert all(p.barcode != "EMPTY1" for p in issues)
