"""Page modules for the ENCOMM ERP Qt application.

Each page inherits from ``BasePage`` and receives ``db_service`` + ``config``
at construction time (wired in during the gradual migration).
"""

from qt_app.pages.base_page import BasePage
from qt_app.pages.dashboard_page import DashboardPage
from qt_app.pages.inventory_page import InventoryPage
from qt_app.pages.suppliers_page import SuppliersPage
from qt_app.pages.pos_page import POSPage
from qt_app.pages.customers_page import CustomersPage
from qt_app.pages.invoice_history_page import InvoiceHistoryPage
from qt_app.pages.stock_movements_page import StockMovementsPage
from qt_app.pages.settings_page import SettingsPage
from qt_app.pages.ai_page import AIPage
from qt_app.pages.goods_receipt_page import GoodsReceiptPage
from qt_app.pages.supplier_reorder_page import SupplierReorderPage

PAGE_CLASSES = {
    "dashboard":             DashboardPage,
    "inventory":             InventoryPage,
    "suppliers":             SuppliersPage,
    "pos":                   POSPage,
    "customers":             CustomersPage,
    "invoice_history":       InvoiceHistoryPage,
    "stock_movements":       StockMovementsPage,
    "settings":              SettingsPage,
    "ai_assistant":          AIPage,
    "goods_receipts":        GoodsReceiptPage,
    "supplier_reorder":      SupplierReorderPage,
}
