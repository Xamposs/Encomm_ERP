from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class Product:
    barcode: str
    name: str
    stock: int
    expiry_date: str  # Format: YYYY-MM-DD
    price: float
    supplier_id: int = None

@dataclass
class Invoice:
    invoice_id: str
    date: str  # Format: YYYY-MM-DD HH:MM:SS
    items: List[Tuple[Product, int]]  # (Product, Quantity)
    vat_amount: float
    total_amount: float
