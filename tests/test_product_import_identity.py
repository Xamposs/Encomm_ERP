"""Tests for import source identity (fingerprinting + verification)."""

import hashlib, pytest
import openpyxl
from datetime import date
from infrastructure.product_import_identity import (
    fingerprint_import_source, verify_import_source,
    ImportSourceSignature,
)
from infrastructure.product_import_preview import ImportColumnMapping


M = ImportColumnMapping("Barcode", "Name", "Stock", "Price", "Expiry")
M2 = ImportColumnMapping("Code", "Product", "Qty", "Cost", "Exp")


def _xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


class TestFingerprint:

    def test_deterministic(self, tmp_path):
        p = str(tmp_path / "a.xlsx")
        _xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [])
        s1 = fingerprint_import_source(p, M)
        s2 = fingerprint_import_source(p, M)
        assert s1 == s2

    def test_file_change_changes_hash(self, tmp_path):
        p = str(tmp_path / "a.xlsx")
        _xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [["A", "N", 1, 1.0, ""]])
        s1 = fingerprint_import_source(p, M)
        # Modify and re-save
        wb = openpyxl.load_workbook(p)
        ws = wb.active
        ws.cell(row=2, column=1, value="B")
        wb.save(p)
        s2 = fingerprint_import_source(p, M)
        assert s1.file_sha256 != s2.file_sha256

    def test_mapping_change_changes_hash(self, tmp_path):
        p = str(tmp_path / "a.xlsx")
        _xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [])
        s1 = fingerprint_import_source(p, M)
        s2 = fingerprint_import_source(p, M2)
        assert s1.mapping_sha256 != s2.mapping_sha256

    def test_large_file_chunked(self, tmp_path):
        p = str(tmp_path / "big.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Barcode", "Name", "Stock", "Price", "Expiry"])
        for i in range(5000):
            ws.append([f"{i:013d}", f"N{i}", 1, 1.0, ""])
        wb.save(p)
        s = fingerprint_import_source(p, M)
        assert len(s.file_sha256) == 64

    def test_verify_match(self, tmp_path):
        p = str(tmp_path / "a.xlsx")
        _xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [])
        s = fingerprint_import_source(p, M)
        assert verify_import_source(s, p, M)

    def test_verify_mismatch(self, tmp_path):
        p = str(tmp_path / "a.xlsx")
        _xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
              [])
        s = fingerprint_import_source(p, M)
        assert not verify_import_source(s, p, M2)

    def test_no_write_sql(self):
        import inspect
        from infrastructure import product_import_identity as pid
        src = inspect.getsource(pid.fingerprint_import_source)
        for pat in ["INSERT", "UPDATE", "DELETE", "DROP",
                     "ALTER", "CREATE", "REPLACE", "sqlite3"]:
            assert pat not in src, f"Forbidden '{pat}' in identity"
