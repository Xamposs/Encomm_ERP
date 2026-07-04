from datetime import datetime, date
from typing import List, Tuple
from .domain_models import Product

def is_low_stock(product: Product, threshold: int = 10) -> bool:
    """Check if the product stock is at or below the low stock threshold."""
    return product.stock <= threshold

def parse_date(date_str: str) -> date:
    """Helper to parse YYYY-MM-DD date strings safely."""
    try:
        # Strip timestamp part if present
        date_only = date_str.strip().split(" ")[0]
        return datetime.strptime(date_only, "%Y-%m-%d").date()
    except Exception:
        # Fallback to a far future date to avoid spurious alerts on malformed input
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
