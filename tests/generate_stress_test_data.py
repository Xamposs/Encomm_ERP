"""
Stress Test Data Generator – 100.000 Mock Pharmacy Products

Produces `supplier_invoice_stress_test.csv` in the project root
with exactly 100.000 unique mock products for bulk import testing.
"""

import csv
import random

ROW_COUNT = 100_000
OUTPUT_FILE = "supplier_invoice_stress_test.csv"


def _mock_ean13(index: int) -> str:
    """Generate a unique-looking EAN-13-style barcode from the row index."""
    base = f"520{index:010d}"
    return base[:13]


def _random_expiry() -> str:
    """Return a random future date between 2026 and 2029."""
    year = random.randint(2026, 2029)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


def main():
    print(f"Generating {ROW_COUNT:,} mock products → {OUTPUT_FILE} ...")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        # Header row
        writer.writerow(["Barcode", "Name", "Stock", "ExpiryDate", "Price"])

        for i in range(1, ROW_COUNT + 1):
            barcode = _mock_ean13(i)
            name = f"Φάρμακο ST-{i}"
            stock = random.randint(0, 100)
            expiry = _random_expiry()
            price = round(random.uniform(2.50, 90.00), 2)
            writer.writerow([barcode, name, stock, expiry, price])

    print(f"✅ Done! {OUTPUT_FILE} created with {ROW_COUNT:,} rows.")


if __name__ == "__main__":
    main()
