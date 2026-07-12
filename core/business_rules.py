from datetime import datetime, date
import logging
from typing import List, Tuple
from .domain_models import Product

def is_low_stock(product: Product, threshold: int = 10) -> bool:
    """Check if the product stock is at or below the low stock threshold."""
    return product.stock <= threshold

def parse_date(date_str: str) -> date:
    """Helper to parse YYYY-MM-DD date strings safely.

    Returns ``date.max`` on malformed/empty input (backward compatible) so
    callers never crash, but **logs a warning** so silent data-quality
    problems — critical in a pharmacy — are visible instead of masked.
    """
    try:
        # Strip timestamp part if present
        date_only = date_str.strip().split(" ")[0]
        return datetime.strptime(date_only, "%Y-%m-%d").date()
    except Exception:
        logging.warning(
            "Unparseable expiry date %r — treating as far-future. "
            "Fix this record to restore correct expiry alerts.", date_str,
        )
        return date.max

def get_days_until_expiry(product: Product, current_date: date = None) -> int:
    """Calculate the number of days until the product expires."""
    if current_date is None:
        current_date = date.today()
    exp_date = parse_date(product.expiry_date)
    return (exp_date - current_date).days

def is_expired(product: Product, current_date: date = None) -> bool:
    """Check if the product is already expired."""
    return get_days_until_expiry(product, current_date) < 0

def is_near_expiry(product: Product, threshold_days: int = 30, current_date: date = None) -> bool:
    """Check if the product is close to its expiry date, within the given threshold days."""
    days = get_days_until_expiry(product, current_date)
    return 0 <= days <= threshold_days

def calculate_vat(amount: float, vat_rate: float) -> float:
    """Calculate VAT amount for a given monetary figure."""
    return round(amount * vat_rate, 2)

def calculate_invoice_totals(items: List[Tuple[Product, int]], vat_rate: float) -> Tuple[float, float]:
    """
    Calculate the total VAT and grand total for a list of products and quantities.
    Returns:
        (vat_amount, grand_total)
    """
    subtotal = sum(product.price * qty for product, qty in items)
    vat_amount = calculate_vat(subtotal, vat_rate)
    grand_total = round(subtotal + vat_amount, 2)
    return vat_amount, grand_total

# ── Greek Pharmacy VAT Categories ────────────────────────────────────

VALID_PHARMACY_VAT_CATEGORIES = (6, 13, 24)

def get_vat_rate(vat_category: int) -> float:
    """Map Greek pharmacy VAT category integer to decimal rate."""
    rates = {6: 0.06, 13: 0.13, 24: 0.24}
    if vat_category not in rates:
        raise ValueError(
            f"Invalid VAT category: {vat_category}. "
            f"Valid values: {VALID_PHARMACY_VAT_CATEGORIES}"
        )
    return rates[vat_category]


# ── Barcode Validation ────────────────────────────────────────────────


def is_valid_ean13(barcode: str) -> bool:
    """Ελέγχει αν ένας γραμμωτός κώδικας είναι έγκυρο EAN-13.
    Επιστρέφει False (δεν εκτοξεύει εξαίρεση) για λάθος μήκος,
    μη-ψηφιακούς χαρακτήρες ή αποτυχία checksum.
    """
    if barcode is None:
        return False
    if not isinstance(barcode, str):
        return False
    barcode = barcode.strip()
    if len(barcode) != 13:
        return False
    if not barcode.isdigit():
        return False

    # EAN-13 checksum: odd positions (1-indexed: 1,3,5,7,9,11) × 1,
    #                  even positions (2,4,6,8,10,12) × 3
    odd_sum = sum(int(barcode[i]) for i in range(0, 12, 2))   # indices 0,2,4,6,8,10
    even_sum = sum(int(barcode[i]) for i in range(1, 12, 2))  # indices 1,3,5,7,9,11
    total = odd_sum + even_sum * 3
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(barcode[12])


def validate_product_barcode(product: Product) -> bool:
    """Επικυρώνει τον γραμμωτό κώδικα ενός προϊόντος με βάση τον τύπο του."""
    if product.barcode is None or product.barcode.strip() == "":
        return False

    barcode_type = product.barcode_type or ""

    if barcode_type == "EAN13":
        return is_valid_ean13(product.barcode)
    elif barcode_type in ("GS1", "CUSTOM", "OTHER"):
        return True
    else:
        # Άγνωστος τύπος — safe default, δεν απορρίπτουμε
        return True
