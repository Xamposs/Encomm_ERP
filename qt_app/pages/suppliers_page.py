"""Suppliers page — μητρώο προμηθευτών."""

from qt_app.pages.base_page import BasePage


class SuppliersPage(BasePage):
    """Supplier registry management."""

    @classmethod
    def page_title(cls) -> str:
        return "Μητρώο Προμηθευτών"
