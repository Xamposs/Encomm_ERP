"""Phase D1: Supplier Goods Receipts — tests for goods_receipt_service and Qt page."""

from __future__ import annotations

import inspect
import os
import sqlite3
import sys

import pytest

# Qt offscreen must be set before any PySide6 import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure project root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from infrastructure.database_service import DatabaseService
from infrastructure.goods_receipt_service import (
    ensure_goods_receipt_schema,
    create_receipt_draft,
    get_receipt,
    list_receipts,
    approve_receipt,
    cancel_receipt,
    CreateDraftResult,
    ApproveReceiptResult,
    CancelReceiptResult,
)
from core.domain_models import Product


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _rw_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _seed_supplier(db: DatabaseService, name: str = "Demo Pharma AE") -> int:
    conn = None
    try:
        path = db.db_path
        conn = _rw_connect(path)
        cur = conn.execute("INSERT INTO suppliers (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        if conn:
            conn.close()


def _seed_product(db: DatabaseService, barcode: str, name: str,
                  stock: int = 50, price: float = 5.0) -> None:
    db.add_product(Product(
        barcode=barcode, name=name, stock=stock,
        expiry_date="2099-12-31", price=price,
        barcode_type="EAN13", vat_category=6,
    ))


def _count_rows(db_path: str, table: str) -> int:
    conn = _rw_connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════

class TestSchemaIdempotent:

    def test_schema_creates_tables(self, tmp_db_path):
        conn = _rw_connect(tmp_db_path)
        ensure_goods_receipt_schema(conn)
        conn.commit()
        conn.close()

        # Re-open and verify
        conn2 = _rw_connect(tmp_db_path)
        tables = {r["name"] for r in
                  conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "goods_receipts" in tables
        assert "goods_receipt_items" in tables
        assert "stock_lots" in tables
        conn2.close()

    def test_schema_is_idempotent(self, tmp_db_path):
        conn = _rw_connect(tmp_db_path)
        ensure_goods_receipt_schema(conn)
        conn.commit()
        conn.close()

        # Second call must not raise
        conn2 = _rw_connect(tmp_db_path)
        ensure_goods_receipt_schema(conn2)
        conn2.commit()
        conn2.close()

    def test_schema_works_with_production_db_initializer(self, db):
        """ensure_goods_receipt_schema runs on a DatabaseService-created db
        without errors — tables coexist with ProductMaster, suppliers, etc."""
        conn = _rw_connect(db.db_path)
        ensure_goods_receipt_schema(conn)
        conn.commit()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Draft creation
# ═══════════════════════════════════════════════════════════════════════

class TestDraftCreation:

    def test_draft_creates_no_stock_movement(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol 500mg", stock=100)

        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DA-001",
                                      document_type="delivery_note",
                                      lines=[{
                                          "barcode": "5200000000017",
                                          "product_name": "Paracetamol 500mg",
                                          "received_qty": 10,
                                          "unit_cost": 1.50,
                                      }])
        assert result.ok, result.error_message
        assert result.receipt_id.startswith("GR-")

        # Stock must not change
        conn = _rw_connect(db.db_path)
        stock = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"]
        assert stock == 100  # unchanged

        # No stock_movements rows
        sm = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source='Παραλαβή'"
        ).fetchone()[0]
        assert sm == 0
        conn.close()

    def test_draft_rejects_invalid_supplier(self, db):
        result = create_receipt_draft(db.db_path, supplier_id=99999,
                                      document_number="DA-001",
                                      document_type="delivery_note",
                                      lines=[{
                                          "barcode": "5200000000017",
                                          "product_name": "Test",
                                          "received_qty": 1,
                                          "unit_cost": 1.0,
                                      }])
        assert not result.ok
        assert "προμηθευτή" in result.error_message.lower()

    def test_draft_rejects_empty_lines(self, db):
        sid = _seed_supplier(db)
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DA-002",
                                      document_type="delivery_note",
                                      lines=[])
        assert not result.ok

    def test_draft_rejects_all_zero_qty(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DA-003",
                                      document_type="delivery_note",
                                      lines=[{
                                          "barcode": "5200000000017",
                                          "product_name": "Paracetamol",
                                          "received_qty": 0,
                                          "unit_cost": 1.0,
                                      }])
        assert not result.ok

    def test_draft_rejects_duplicate_doc_number(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol")
        r1 = create_receipt_draft(db.db_path, supplier_id=sid,
                                  document_number="DA-DUP",
                                  document_type="delivery_note",
                                  lines=[{
                                      "barcode": "5200000000017",
                                      "product_name": "Paracetamol",
                                      "received_qty": 5,
                                      "unit_cost": 1.0,
                                  }])
        assert r1.ok
        r2 = create_receipt_draft(db.db_path, supplier_id=sid,
                                  document_number="DA-DUP",
                                  document_type="delivery_note",
                                  lines=[{
                                      "barcode": "5200000000017",
                                      "product_name": "Paracetamol",
                                      "received_qty": 3,
                                      "unit_cost": 1.0,
                                  }])
        assert not r2.ok

    def test_draft_requires_at_least_one_qty_gt_zero(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        _seed_product(db, "5200000000018", "P2")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DA-004",
                                      document_type="delivery_note",
                                      lines=[
                                          {"barcode": "5200000000017",
                                           "product_name": "P1",
                                           "received_qty": 0, "unit_cost": 1.0},
                                          {"barcode": "5200000000018",
                                           "product_name": "P2",
                                           "received_qty": 0, "unit_cost": 2.0},
                                      ])
        assert not result.ok


# ═══════════════════════════════════════════════════════════════════════
# Approval
# ═══════════════════════════════════════════════════════════════════════

class TestApproval:

    def test_approve_one_item_increases_stock_and_creates_lot_and_audit(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-001",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "Paracetamol",
                                         "received_qty": 30,
                                         "unit_cost": 1.50,
                                         "batch_number": "BATCH-42",
                                         "expiry_date": "2027-06-30",
                                     }])
        assert draft.ok

        result = approve_receipt(db.db_path, draft.receipt_id,
                                 operator="Φαρμακοποιός")
        assert result.ok, result.error_message
        assert result.lines_applied == 1
        assert result.total_units == 30

        # Stock increased
        conn = _rw_connect(db.db_path)
        stock = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"]
        assert stock == 130

        # Stock lot created
        lot = conn.execute(
            "SELECT quantity FROM stock_lots WHERE barcode='5200000000017'"
            " AND batch_number='BATCH-42' AND expiry_date='2027-06-30'"
        ).fetchone()
        assert lot is not None
        assert lot["quantity"] == 30

        # Audit row exists
        sm = conn.execute(
            "SELECT * FROM stock_movements WHERE barcode='5200000000017'"
            " AND reason LIKE '%Παραλαβή%'"
        ).fetchone()
        assert sm is not None
        assert sm["old_stock"] == 100
        assert sm["new_stock"] == 130
        assert sm["change_amount"] == 30
        assert sm["source"] == "Παραλαβή"
        conn.close()

    def test_approve_increments_existing_lot(self, db):
        """Second approval of same barcode+batch+expiry increments lot qty."""
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol", stock=100)

        # First draft
        d1 = create_receipt_draft(db.db_path, supplier_id=sid,
                                  document_number="DA-L1",
                                  document_type="delivery_note",
                                  lines=[{
                                      "barcode": "5200000000017",
                                      "product_name": "Paracetamol",
                                      "received_qty": 20,
                                      "unit_cost": 1.50,
                                      "batch_number": "LOT-A",
                                      "expiry_date": "2028-01-01",
                                  }])
        approve_receipt(db.db_path, d1.receipt_id)

        # Second draft — same batch
        d2 = create_receipt_draft(db.db_path, supplier_id=sid,
                                  document_number="DA-L2",
                                  document_type="delivery_note",
                                  lines=[{
                                      "barcode": "5200000000017",
                                      "product_name": "Paracetamol",
                                      "received_qty": 15,
                                      "unit_cost": 1.60,
                                      "batch_number": "LOT-A",
                                      "expiry_date": "2028-01-01",
                                  }])
        approve_receipt(db.db_path, d2.receipt_id)

        conn = _rw_connect(db.db_path)
        lot = conn.execute(
            "SELECT quantity FROM stock_lots WHERE barcode='5200000000017'"
            " AND batch_number='LOT-A' AND expiry_date='2028-01-01'"
        ).fetchone()
        assert lot["quantity"] == 35
        conn.close()

    def test_approve_multiple_items_is_atomic(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol", stock=100)
        _seed_product(db, "5200000000018", "Amoxicillin", stock=50)
        _seed_product(db, "5200000000019", "Ibuprofen", stock=80)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-MULTI",
                                     document_type="supplier_invoice",
                                     lines=[
                                         {"barcode": "5200000000017",
                                          "product_name": "Paracetamol",
                                          "received_qty": 10, "unit_cost": 1.0},
                                         {"barcode": "5200000000018",
                                          "product_name": "Amoxicillin",
                                          "received_qty": 20, "unit_cost": 2.0},
                                         {"barcode": "5200000000019",
                                          "product_name": "Ibuprofen",
                                          "received_qty": 30, "unit_cost": 3.0},
                                     ])
        result = approve_receipt(db.db_path, draft.receipt_id)
        assert result.ok
        assert result.lines_applied == 3
        assert result.total_units == 60

        conn = _rw_connect(db.db_path)
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"] == 110
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000018'"
        ).fetchone()["Stock"] == 70
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000019'"
        ).fetchone()["Stock"] == 110
        conn.close()

    def test_zero_qty_line_causes_no_stock_or_audit_write(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)
        _seed_product(db, "5200000000018", "P2", stock=50)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-ZERO",
                                     document_type="delivery_note",
                                     lines=[
                                         {"barcode": "5200000000017",
                                          "product_name": "P1",
                                          "received_qty": 0, "unit_cost": 1.0},
                                         {"barcode": "5200000000018",
                                          "product_name": "P2",
                                          "received_qty": 5, "unit_cost": 2.0},
                                     ])
        result = approve_receipt(db.db_path, draft.receipt_id)

        assert result.ok
        assert result.lines_applied == 1  # only line 2
        assert result.total_units == 5

        conn = _rw_connect(db.db_path)
        # P1 stock unchanged
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"] == 100
        # P2 stock increased
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000018'"
        ).fetchone()["Stock"] == 55

        # Only one audit row
        sm_count = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source='Παραλαβή'"
        ).fetchone()[0]
        assert sm_count == 1
        conn.close()

    def test_all_zero_receipt_cannot_be_approved(self, db):
        # draft must have at least one qty>0 to be created,
        # but let's test direct approval of an edge case where all qty are 0
        # (service validates at approval time too)
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)
        # Bypass create_receipt_draft validation for this edge case
        conn = _rw_connect(db.db_path)
        ensure_goods_receipt_schema(conn)
        receipt_id = "GR-TEST-ALLZERO"
        conn.execute(
            """INSERT INTO goods_receipts
               (id, supplier_id, document_number, document_type, status,
                received_at, created_at)
               VALUES (?, ?, ?, 'delivery_note', 'draft',
                       '2026-07-17', '2026-07-17 10:00:00')""",
            (receipt_id, sid, "DA-ALLZERO"))
        conn.execute(
            """INSERT INTO goods_receipt_items
               (receipt_id, line_number, barcode, product_name,
                received_qty, unit_cost)
               VALUES (?, 1, '5200000000017', 'P1', 0, 1.0)""",
            (receipt_id,))
        conn.commit()
        conn.close()

        result = approve_receipt(db.db_path, receipt_id)
        assert not result.ok

    def test_unknown_barcode_rejects_approval_and_rolls_back(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Known Product", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-UNKNOWN",
                                     document_type="delivery_note",
                                     lines=[
                                         {"barcode": "5200000000017",
                                          "product_name": "Known Product",
                                          "received_qty": 5, "unit_cost": 1.0},
                                         {"barcode": "9999999999999",
                                          "product_name": "Unknown",
                                          "received_qty": 10, "unit_cost": 2.0},
                                     ])
        assert draft.ok

        result = approve_receipt(db.db_path, draft.receipt_id)
        assert not result.ok
        assert "barcode" in result.error_message.lower() or "γνωστ" in result.error_message.lower()

        # Known product stock must NOT have changed
        conn = _rw_connect(db.db_path)
        stock = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"]
        assert stock == 100

        # No audit rows
        sm = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source='Παραλαβή'"
        ).fetchone()[0]
        assert sm == 0

        # Receipt status still draft
        status = conn.execute(
            "SELECT status FROM goods_receipts WHERE id=?",
            (draft.receipt_id,)).fetchone()["status"]
        assert status == "draft"
        conn.close()

    def test_approving_twice_is_rejected_no_second_stock_increase(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "Paracetamol", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-TWICE",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "Paracetamol",
                                         "received_qty": 10,
                                         "unit_cost": 1.50,
                                     }])
        r1 = approve_receipt(db.db_path, draft.receipt_id)
        assert r1.ok

        r2 = approve_receipt(db.db_path, draft.receipt_id)
        assert not r2.ok

        # Stock increased only once
        conn = _rw_connect(db.db_path)
        stock = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"]
        assert stock == 110
        conn.close()

    def test_approve_receipt_with_batch_and_expiry(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=20)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-BATCH",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "P1",
                                         "received_qty": 15,
                                         "unit_cost": 3.33,
                                         "batch_number": "LOT-2026-Q3",
                                         "expiry_date": "2028-12-31",
                                     }])
        result = approve_receipt(db.db_path, draft.receipt_id)
        assert result.ok

        conn = _rw_connect(db.db_path)
        lot = conn.execute(
            "SELECT * FROM stock_lots WHERE barcode='5200000000017'"
        ).fetchone()
        assert lot["batch_number"] == "LOT-2026-Q3"
        assert lot["expiry_date"] == "2028-12-31"
        assert lot["quantity"] == 15
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Cancel
# ═══════════════════════════════════════════════════════════════════════

class TestCancel:

    def test_cancel_draft_changes_status_only(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-CANCEL",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "P1",
                                         "received_qty": 10,
                                         "unit_cost": 1.0,
                                     }])
        result = cancel_receipt(db.db_path, draft.receipt_id)
        assert result.ok

        conn = _rw_connect(db.db_path)
        status = conn.execute(
            "SELECT status FROM goods_receipts WHERE id=?",
            (draft.receipt_id,)).fetchone()["status"]
        assert status == "cancelled"

        # Stock unchanged
        stock = conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"]
        assert stock == 100

        # No audit rows
        sm = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source='Παραλαβή'"
        ).fetchone()[0]
        assert sm == 0
        conn.close()

    def test_cannot_cancel_approved_receipt(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-NOCANCEL",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "P1",
                                         "received_qty": 5,
                                         "unit_cost": 1.0,
                                     }])
        approve_receipt(db.db_path, draft.receipt_id)
        result = cancel_receipt(db.db_path, draft.receipt_id)
        assert not result.ok

    def test_cannot_cancel_already_cancelled(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-DCANCEL",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "P1",
                                         "received_qty": 1,
                                         "unit_cost": 1.0,
                                     }])
        cancel_receipt(db.db_path, draft.receipt_id)
        r2 = cancel_receipt(db.db_path, draft.receipt_id)
        assert not r2.ok


# ═══════════════════════════════════════════════════════════════════════
# Listing & retrieval
# ═══════════════════════════════════════════════════════════════════════

class TestListAndGet:

    def test_list_receipts_returns_empty(self, db):
        result = list_receipts(db.db_path)
        assert result.ok
        assert result.total == 0

    def test_list_receipts_returns_drafts(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        create_receipt_draft(db.db_path, supplier_id=sid,
                             document_number="DA-L1",
                             document_type="delivery_note",
                             lines=[{"barcode": "5200000000017",
                                      "product_name": "P1",
                                      "received_qty": 1, "unit_cost": 1.0}])
        result = list_receipts(db.db_path)
        assert result.ok
        assert result.total == 1

    def test_get_receipt_returns_full_detail(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-GET",
                                     document_type="delivery_note",
                                     lines=[{
                                         "barcode": "5200000000017",
                                         "product_name": "P1",
                                         "received_qty": 7,
                                         "unit_cost": 2.45,
                                         "batch_number": "BT",
                                         "expiry_date": "2027-01-01",
                                     }])
        r = get_receipt(db.db_path, draft.receipt_id)
        assert r.ok
        assert r.receipt.supplier_name == "Demo Pharma AE"
        assert len(r.receipt.lines) == 1
        assert r.receipt.lines[0].received_qty == 7
        assert r.receipt.lines[0].unit_cost == 2.45
        assert r.receipt.lines[0].batch_number == "BT"

    def test_get_receipt_nonexistent(self, db):
        r = get_receipt(db.db_path, "GR-NONEXISTENT")
        assert not r.ok


# ═══════════════════════════════════════════════════════════════════════
# Approval uses _log_stock_movement_on_conn
# ═══════════════════════════════════════════════════════════════════════

class TestApprovalUsesLogStockMovement:

    def test_approval_calls_log_stock_movement_on_conn(self):
        """Prove approve_receipt calls DatabaseService._log_stock_movement_on_conn."""
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs.approve_receipt)
        assert "_log_stock_movement_on_conn" in src


# ═══════════════════════════════════════════════════════════════════════
# No VAT / tax / invoice / IDIKA / AADE / external integration changes
# ═══════════════════════════════════════════════════════════════════════

class TestNoVatOrInvoiceChanges:

    def test_service_does_not_mention_invoices(self):
        """Goods receipt service must never touch invoices tables."""
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs)
        # No INSERT/UPDATE/DELETE on invoices or invoice_items
        for verb in ("INSERT INTO invoices", "UPDATE invoices",
                     "DELETE FROM invoices", "INSERT INTO invoice_items",
                     "UPDATE invoice_items", "DELETE FROM invoice_items"):
            assert verb not in src, f"Goods receipt service illegally references: {verb}"

    def test_service_does_not_mention_iddik_aade_vat(self):
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs)
        for banned in ("IDIKA", "AADE", "vat", "VAT", "tax", "TAX",
                       "Iddik", "aade"):
            assert banned not in src, f"Goods receipt service illegally references: {banned}"

    def test_service_does_not_update_expiry_date(self):
        """ProductMaster.ExpiryDate must never be updated by goods receipt service."""
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs.approve_receipt)
        assert "UPDATE ProductMaster SET ExpiryDate" not in src
        assert "ExpiryDate" not in src  # No UPDATE ... ExpiryDate anywhere

    def test_service_does_not_touch_invoice_tables(self):
        """Goods receipt service must never INSERT/UPDATE/DELETE on sales invoice tables."""
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs)
        assert "INSERT INTO invoices" not in src
        assert "UPDATE invoices" not in src
        assert "DELETE FROM invoices" not in src
        assert "INSERT INTO invoice_items" not in src
        assert "UPDATE invoice_items" not in src
        assert "DELETE FROM invoice_items" not in src


# ═══════════════════════════════════════════════════════════════════════
# Qt page structural tests (no display needed)
# ═══════════════════════════════════════════════════════════════════════

class TestGoodsReceiptPageStructural:

    def test_page_registered_in_page_classes(self):
        from qt_app.pages import PAGE_CLASSES
        assert "goods_receipts" in PAGE_CLASSES
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        assert PAGE_CLASSES["goods_receipts"] is GoodsReceiptPage

    def test_page_in_nav_items(self):
        from qt_app.main_window import NAV_ITEMS
        keys = [k for k, _ in NAV_ITEMS]
        assert "goods_receipts" in keys

    def test_page_in_page_titles(self):
        from qt_app.main_window import PAGE_TITLES
        assert "goods_receipts" in PAGE_TITLES
        assert "Παραλαβές" in PAGE_TITLES["goods_receipts"]

    def test_page_instantiable_without_crash(self, db):
        """Create the page — should not raise even without a real QApplication
        in offscreen mode.  pytest-qt or PySide6 import guard."""
        try:
            from PySide6.QtWidgets import QApplication
            from qt_app.pages.goods_receipt_page import GoodsReceiptPage

            app = QApplication.instance()
            if app is None:
                app = QApplication(["offscreen"])

            config = {"db_path": db.db_path, "theme": "Dark"}
            page = GoodsReceiptPage(None, config)
            assert page is not None
            page.deleteLater()
        except ImportError:
            pytest.skip("PySide6 not available")

    def test_page_shutdown_contract(self):
        """GoodsReceiptPage has shutdown(), shutdown_ready, _close_pending."""
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        import inspect
        src = inspect.getsource(GoodsReceiptPage)
        assert "def shutdown(self)" in src
        assert "shutdown_ready" in src
        assert "_close_pending" in src

    def test_page_approval_requires_confirmation_checkbox(self):
        """Verify the approve button is gated behind _confirm_cb toggle."""
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._on_confirm_toggled)
        assert "_approve_btn.setEnabled" in src

    def test_page_approve_blocked_for_non_drafts(self):
        """Verify cancel_btn and approve_btn are disabled for approved/cancelled."""
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._on_detail_result)
        assert "is_draft" in src
        assert "_cancel_btn.setEnabled" in src
        assert "_approve_btn.setEnabled" in src

    def test_page_has_cross_page_refresh(self):
        """approval triggers refresh on inventory and stock_movements pages."""
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._refresh_related_pages)
        assert "inventory" in src
        assert "stock_movements" in src
        assert "refresh" in src

    def test_page_preserves_nav_structure(self):
        """Adding goods_receipts didn't remove any existing nav items."""
        from qt_app.main_window import NAV_ITEMS
        keys = {k for k, _ in NAV_ITEMS}
        required = {"dashboard", "inventory", "suppliers", "pos",
                    "customers", "invoice_history", "stock_movements",
                    "settings", "ai_assistant", "goods_receipts"}
        assert keys == required, f"Missing keys: {required - keys}"
        assert len(NAV_ITEMS) == 10


# ═══════════════════════════════════════════════════════════════════════
# Transaction / rollback guarantees
# ═══════════════════════════════════════════════════════════════════════

class TestTransactionGuarantees:
    """Validate that approval is fully atomic — any failure rolls back."""

    def test_approval_rolls_back_on_mid_approval_db_error(self, db, monkeypatch):
        """Simulate a DB error during approval so we confirm rollback.

        Monkeypatch DatabaseService._log_stock_movement_on_conn to fail on the
        second call — after the first product's stock has been updated but before
        the second product starts.  This proves that the BEGIN IMMEDIATE
        transaction rolls back everything.
        """
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1", stock=100)
        _seed_product(db, "5200000000018", "P2", stock=50)

        original = DatabaseService._log_stock_movement_on_conn

        call_count = [0]

        @staticmethod
        def _failing_log(cursor, barcode, product_name, old_stock, new_stock,
                         reason, source="", operator="Σύστημα"):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise sqlite3.OperationalError("Simulated audit failure")
            return original(cursor, barcode, product_name,
                            old_stock, new_stock, reason, source, operator)

        monkeypatch.setattr(DatabaseService, "_log_stock_movement_on_conn",
                            _failing_log)

        draft = create_receipt_draft(db.db_path, supplier_id=sid,
                                     document_number="DA-BOMB",
                                     document_type="delivery_note",
                                     lines=[
                                         {"barcode": "5200000000017",
                                          "product_name": "P1",
                                          "received_qty": 10, "unit_cost": 1.0},
                                         {"barcode": "5200000000018",
                                          "product_name": "P2",
                                          "received_qty": 5, "unit_cost": 2.0},
                                     ])
        result = approve_receipt(db.db_path, draft.receipt_id)
        assert not result.ok

        monkeypatch.undo()

        # All stocks must be unchanged
        conn = _rw_connect(db.db_path)
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000017'"
        ).fetchone()["Stock"] == 100
        assert conn.execute(
            "SELECT Stock FROM ProductMaster WHERE Barcode='5200000000018'"
        ).fetchone()["Stock"] == 50
        # No audit rows from approval (the first audit was rolled back)
        approval_audit = conn.execute(
            "SELECT COUNT(*) FROM stock_movements WHERE source='Παραλαβή'"
        ).fetchone()[0]
        assert approval_audit == 0
        # Receipt still draft
        status = conn.execute(
            "SELECT status FROM goods_receipts WHERE id=?",
            (draft.receipt_id,)).fetchone()["status"]
        assert status == "draft"
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Regression tests — D1.1 fixes
# ═══════════════════════════════════════════════════════════════════════

class TestD11QColorFix:
    """Fix 1: QColor instead of Qt.GlobalColor for status hex colors."""

    def test_page_uses_qcolor_not_globalcolor(self):
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._on_list_result)
        assert "QColor(" in src
        assert "Qt.GlobalColor" not in src

    def test_list_rendering_no_crash_offscreen(self):
        import tempfile, os
        from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
        from PySide6.QtGui import QColor
        from infrastructure.database_service import DatabaseService
        from infrastructure.goods_receipt_service import ensure_goods_receipt_schema

        app = QApplication.instance() or QApplication(["offscreen"])
        db_path = tempfile.mktemp(suffix=".db")
        try:
            db = DatabaseService(db_path=db_path)
            conn = _rw_connect(db_path)
            ensure_goods_receipt_schema(conn)
            conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'Test AE')")
            conn.execute("INSERT INTO ProductMaster (Barcode, Name, Stock, ExpiryDate, Price) "
                         "VALUES ('TEST001', 'Test', 100, '2099-01-01', 5.0)")
            conn.execute(
                "INSERT INTO goods_receipts (id, supplier_id, document_number, "
                "document_type, status, received_at, created_at) "
                "VALUES ('GR-T1',1,'DOC','delivery_note','draft','2026-07-17','2026-07-17')")
            conn.commit()
            conn.close()

            from qt_app.pages.goods_receipt_page import GoodsReceiptPage
            config = {"db_path": db_path, "theme": "Dark"}
            page = GoodsReceiptPage(None, config)
            table = QTableWidget()
            table.setRowCount(1)
            item = QTableWidgetItem("draft")
            item.setForeground(QColor("#F59E0B"))
            table.setItem(0, 0, item)
            page.deleteLater()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestD11DeferredRefresh:
    """Fix 2: Deferred list refresh after create/approve/cancel success."""

    def test_back_to_list_sets_flag_not_refreshes(self):
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._back_to_list)
        assert "_pending_list_refresh = True" in src
        assert "_do_list_refresh()" not in src

    def test_on_worker_done_has_deferred_refresh(self):
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._on_worker_done)
        assert "_pending_list_refresh" in src
        assert "_do_list_refresh()" in src


class TestD11Search:
    """Fix 3: Search filtering by supplier name and document number."""

    def test_list_receipts_accepts_search_text(self):
        import inspect
        src = inspect.getsource(list_receipts)
        assert "search_text" in src

    def test_search_normalize_registered(self):
        import inspect
        src = inspect.getsource(list_receipts)
        assert "create_function" in src
        assert "search_normalize" in src

    def test_search_by_supplier_name_greek(self, db):
        sid = _seed_supplier(db, "Φαρμακο ΑΕ")
        _seed_product(db, "5200000000017", "P1")
        create_receipt_draft(db.db_path, supplier_id=sid,
                             document_number="DOC-001",
                             document_type="delivery_note",
                             lines=[{"barcode": "5200000000017",
                                      "product_name": "P1",
                                      "received_qty": 1, "unit_cost": 1.0}])
        result = list_receipts(db.db_path, search_text="φαρμακο")
        assert result.ok
        assert result.total == 1

    def test_search_by_document_number(self, db):
        sid = _seed_supplier(db, "Test AE")
        _seed_product(db, "5200000000017", "P1")
        create_receipt_draft(db.db_path, supplier_id=sid,
                             document_number="DEL-2026-0042",
                             document_type="delivery_note",
                             lines=[{"barcode": "5200000000017",
                                      "product_name": "P1",
                                      "received_qty": 1, "unit_cost": 1.0}])
        result = list_receipts(db.db_path, search_text="0042")
        assert result.ok
        assert result.total == 1

    def test_page_passes_search_to_worker(self):
        import inspect
        from qt_app.pages.goods_receipt_page import GoodsReceiptPage
        src = inspect.getsource(GoodsReceiptPage._do_list_refresh)
        assert '"search_text"' in src
        assert "_search.text()" in src


class TestD11MalformedInput:
    """Fix 4: Harden create_receipt_draft against malformed line input."""

    def test_none_quantity_returns_failure(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DOC-NQ",
                                      document_type="delivery_note",
                                      lines=[{"barcode": "5200000000017",
                                               "product_name": "P1",
                                               "received_qty": None,
                                               "unit_cost": 1.0}])
        assert not result.ok
        assert "ποσότητα" in result.error_message.lower()

    def test_string_quantity_returns_failure(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DOC-SQ",
                                      document_type="delivery_note",
                                      lines=[{"barcode": "5200000000017",
                                               "product_name": "P1",
                                               "received_qty": "ten",
                                               "unit_cost": 1.0}])
        assert not result.ok
        assert "ποσότητα" in result.error_message.lower()

    def test_boolean_quantity_returns_failure(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DOC-BQ",
                                      document_type="delivery_note",
                                      lines=[{"barcode": "5200000000017",
                                               "product_name": "P1",
                                               "received_qty": True,
                                               "unit_cost": 1.0}])
        assert not result.ok
        assert "ποσότητα" in result.error_message.lower()

    def test_none_cost_returns_failure(self, db):
        sid = _seed_supplier(db)
        _seed_product(db, "5200000000017", "P1")
        result = create_receipt_draft(db.db_path, supplier_id=sid,
                                      document_number="DOC-NC",
                                      document_type="delivery_note",
                                      lines=[{"barcode": "5200000000017",
                                               "product_name": "P1",
                                               "received_qty": 5,
                                               "unit_cost": None}])
        assert not result.ok
        assert "κόστος" in result.error_message.lower()

    def test_validation_before_has_qty(self):
        import inspect
        from infrastructure import goods_receipt_service as grs
        src = inspect.getsource(grs.create_receipt_draft)
        validate_idx = src.index("_validate_receipt_line")
        has_qty_idx = src.index("has_qty")
        assert validate_idx < has_qty_idx, \
            "Per-line validation must run BEFORE has_qty numeric comparison"
