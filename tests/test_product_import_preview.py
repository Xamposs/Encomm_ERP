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
        assert m.price_column == "Unit Price"

    def test_greek_suggestion(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Κωδικός", "Όνομα", "Απόθεμα", "Τιμή", "Λήξη"], [])
        m = suggest_mapping(p)
        assert m is not None

    def test_duplicate_header_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Barcode", "Stock", "Price", "Expiry"],
                     [["A", "A", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert not r.ok
        assert "διπλότυπα" in r.error_message

    def test_reused_column_rejected(self, tmp_path):
        r = preview_product_import_xlsx("fake.xlsx", ImportColumnMapping(
            "ColA", "ColA", "ColB", "ColC", "ColD"))
        assert not r.ok
        assert "μοναδική" in r.error_message

    def test_explicit_mapping(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["ColA", "ColB", "ColC", "ColD", "ColE"],
                     [["5200000000001", "Test", 10, 5.0, ""]])
        r = preview_product_import_xlsx(p, ImportColumnMapping(
            "ColA", "ColB", "ColC", "ColD", "ColE"))
        assert r.ok
        assert r.valid_rows == 1

    def test_missing_mapped_column(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price"],
                     [["A", "T", 1, 1.0]])
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

    def test_leading_zero_barcode(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["0001234567890", "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][0] == "0001234567890"

    def test_numeric_barcode_15_digit_limit(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [[1234567890123456, "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1
        assert "15" in r.errors[0].message

    def test_bool_barcode_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [[True, "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_nan_barcode_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [[float("nan"), "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_inf_barcode_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [[float("inf"), "T", 1, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_stock_float_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1.5, 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1
        assert "ακέραιο" in r.errors[0].message

    def test_nan_stock_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", float("nan"), 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_inf_stock_rejected(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", float("inf"), 1.0, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.invalid_rows == 1

    def test_blank_expiry(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.0, None]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][4] == ""

    def test_date_expiry(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        from datetime import date
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.0, date(2027, 12, 31)]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][4] == "2027-12-31"

    def test_price_not_rounded(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["A", "T", 1, 1.23456, ""]])
        r = preview_product_import_xlsx(p, M)
        assert r.ok
        assert r.sample_rows[0][3] == 1.23456  # exact, not 1.23

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
        assert not r.ok
        assert r.cancelled
        assert "ακυρώθηκε" in r.error_message
        assert r.scanned_rows < 30

    def test_max_rows_limit(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        rows = [["A", f"T{i}", 1, 1.0, ""] for i in range(20)]
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"], rows)
        r = preview_product_import_xlsx(p, M, max_rows=10)
        assert not r.ok
        assert not r.cancelled
        assert "Υπέρβαση" in r.error_message
        assert r.scanned_rows == 10


class TestErrors:

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
        p = str(tmp_path / "t.xlsx")
        _write_xlsx(p, ["Barcode", "Name", "Stock", "Price", "Expiry"],
                     [["", "", -1, -1.0, ""]] * 350)
        r = preview_product_import_xlsx(p, M)
        assert len(r.errors) <= 200
        assert r.valid_rows == 0


class TestNoWrite:

    def test_no_write_sql_or_db(self):
        import inspect
        from infrastructure import product_import_preview as pip
        src = inspect.getsource(pip.preview_product_import_xlsx)
        for pat in ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                     "ALTER ", "CREATE TABLE", "REPLACE ", "sqlite3"]:
            assert pat not in src, f"Forbidden '{pat}' in import preview"

    def test_read_only_flag(self):
        import inspect
        from infrastructure import product_import_preview as pip
        src = inspect.getsource(pip.preview_product_import_xlsx)
        assert "read_only=True" in src
