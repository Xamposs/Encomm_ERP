"""Customers page — μητρώο πελατών."""

from qt_app.pages.base_page import BasePage


class CustomersPage(BasePage):
    """Customer registry management."""

    @classmethod
    def page_title(cls) -> str:
        return "Μητρώο Πελατών"
