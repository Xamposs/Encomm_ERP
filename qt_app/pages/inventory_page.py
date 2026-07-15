"""Inventory page — διαχείριση αποθήκης."""

from qt_app.pages.base_page import BasePage


class InventoryPage(BasePage):
    """Product inventory with search, filters, and table."""

    @classmethod
    def page_title(cls) -> str:
        return "Διαχείριση Αποθήκης"
