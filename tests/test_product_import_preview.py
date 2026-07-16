"""Tests for product import preview — creates real .xlsx files via openpyxl."""

import pytest, openpyxl
from datetime import date
from infrastructure.product_import_preview import (
    preview_product_import_xlsx, ImportColumnMapping,
    suggest_mapping, list_xlsx_sheets, inspect_xlsx_headers,
    ImportRowError, ProductImportPreview,
)


def _write_xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


M = ImportColumnMapping("Barcode", "Name", "Stock", "Price", "Expiry")


class TestHeaderMapping:

    def test_english_suggestion(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Unit Price", "Expiry Date"], [])
        m = suggest_mapping(p)
        assert m is not None
        assert m.barcode_column == "Barcode"
        assert m.price_column == "Unit Price"

    def test_greek_suggestion(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Κωδικός", "Όνομα", "Απόθεμα", "Τιμή", "Λήξη"], [])
        m = suggest_mapping(p)
        assert m is not None
        assert m.barcode_column == "Κωδικός"

    def test_explicit_mapping(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["ColA", "ColB", "ColC", "ColD", "ColE"],
                     [["5200000000001", "Test", 10, 5.0, ""]])
        r = preview_product_import_xlsx(p, ImportColumnMapping(
            "ColA", "ColB", "ColC", "ColD", "ColE"))
        assert r.ok
        assert r.valid_rows == 1

    def test_missing_mapping_column(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price"],  # no Expiry
                     [["5200000000001", "T", 1, 1.0]])
        r = preview_product_import_xlsx(p, M)
        assert not r.ok
        assert "στήλη" in r.error_message


class TestValidation:

    def test_valid_rows(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["5200000000001", "Ασπιρίνη", 50, 5.0, "2027-12-31"],
                      ["5200000000002", "Depon", 30, 3.5, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.valid_rows == 2
        assert len(r.sample_rows) == 2

    def test_leading_zero_barcode(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["0001234567890", "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][0] == "0001234567890"

    def test_blank_expiry(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.0, None]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][4] == ""

    def test_date_expiry(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.0, date(2027, 12, 31)]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][4] == "2027-12-31"

    def test_invalid_stock(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", -1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_invalid_price(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, -5.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_invalid_expiry(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.0, "not-a-date"]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_empty_barcode(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["", "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_empty_name(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_duplicate_barcode(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "First", 1, 1.0, ""],
                      ["A", "Second", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.duplicate_barcodes == 1

    def test_bounded_errors_and_samples(self, tmp_path):
        """300 invalid rows → max 200 errors; 50 valid → max 20 samples."""
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["", "", -1, -1.0, ""]] * 350)
        r = preview_product_import_xlsx(p, M)
        assert len(r.errors) <= 200
        assert r.valid_rows == 0

    def test_cancellation(self, tmp_path):
        class Cancel:
            def __init__(self):
                self.called = 0
            def is_set(self):
                self.called += 1
                return self.called > 5
        p = str(tmp_path / "t.xlsx")
        rows = [["A", f"T{i}", 1, 1.0, ""] for i in range(100)]
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        cancel = Cancel()
        r = preview_product_import_xlsx(p, M, cancel_event=cancel)
        assert r.ok
        assert r.scanned_rows < 30  # stopped early after header + ~5

    def test_no_write_sql(self):
        import inspect
        from infrastructure import product_import_preview as pip
        src = inspect.getsource(pip.preview_product_import_xlsx)
        patterns = ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE ", "sqlite3", "connect"]
        for pat in patterns:
            assert pat not in src, f"Forbidden '{pat}' in import preview"

    def test_read_only_flag(self):
        """Verify read_only=True is in the source."""
        import inspect
        from infrastructure import product_import_preview as pip
        src = inspect.getsource(pip.preview_product_import_xlsx)
        assert "read_only=True" in src
