"""Shared import configuration constants (B1 + C1).

Import-safe — no database, no openpyxl, no Qt.  Safe to import from
any module without circular-dependency risk.
"""

MAX_IMPORT_ROWS = 250_000
