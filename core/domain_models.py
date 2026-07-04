from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

@dataclass
class Product:
    barcode: str
    name: str
    stock: int
    expiry_date: str  # Format: YYYY-MM-DD
    price: float
    supplier_id: int = None
    # NEW FIELDS (added with defaults — existing code does not break)
    supplier_code: Optional[str] = None
    barcode_type: str = "EAN13"       # "EAN13" | "GS1" | "CUSTOM" | "OTHER"
    vat_category: int = 6             # 6, 13, or 24 — Greek pharmacy VAT tiers
    eof_code: Optional[str] = None    # ΕΟΦ national medicines registration code

@dataclass
class Supplier:
    id: Optional[int] = None
    name: str = ""
    tax_id: str = ""                          # ΑΦΜ
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    allowed_sender_emails: list = field(default_factory=list)  # stored as JSON
    catalogue_format: str = "XLSX"            # "XLSX" | "CSV" | "PDF" | "PORTAL"
    default_markup: float = 0.25
    pricing_notes: Optional[str] = None
    created_at: Optional[str] = None

@dataclass
class Invoice:
    invoice_id: str
    date: str  # Format: YYYY-MM-DD HH:MM:SS
    items: List[Tuple[Product, int]]  # (Product, Quantity)
    vat_amount: float
    total_amount: float
