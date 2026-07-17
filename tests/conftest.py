"""Shared pytest fixtures for ENCOMM ERP tests.

Tests run against a throwaway SQLite database in a temp directory so
they never touch the production pharmacy.db.
"""
import os
import sys
import tempfile

import pytest

# Qt offscreen — must be set before any PySide6 import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure the project root is importable when running `pytest` from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.domain_models import Product
from infrastructure.database_service import DatabaseService


@pytest.fixture()
def tmp_db_path(tmp_path):
    """Return a path for a throwaway SQLite DB file."""
    return str(tmp_path / "test_erp.db")


@pytest.fixture()
def db(tmp_db_path):
    """A fresh DatabaseService backed by an empty temp DB."""
    service = DatabaseService(db_path=tmp_db_path)
    yield service
    # Drop all seed data between tests so each starts from a known state.
    conn = None
    try:
        conn = sqlite_connect(tmp_db_path)
        conn.executescript(
            "DELETE FROM invoice_items; DELETE FROM invoices; "
            "DELETE FROM stock_movements; DELETE FROM ProductMaster; "
            "DELETE FROM customers; DELETE FROM suppliers;"
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


@pytest.fixture()
def sample_product():
    """A product with plenty of stock, far-future expiry."""
    return Product(
        barcode="5200000000017",
        name="Παρακεταμόλη",
        stock=100,
        expiry_date="2099-12-31",
        price=3.50,
        barcode_type="EAN13",
        vat_category=6,
    )


@pytest.fixture()
def seeded_db(db, sample_product):
    """A DB with one product already inserted."""
    db.add_product(sample_product)
    return db


def sqlite_connect(path):
    import sqlite3
    conn = sqlite3.connect(path)
    return conn
