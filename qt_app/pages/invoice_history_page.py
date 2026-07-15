"""Invoice history page — ιστορικό παραστατικών."""

from qt_app.pages.base_page import BasePage


class InvoiceHistoryPage(BasePage):
    """Historical invoice browser and search."""

    @classmethod
    def page_title(cls) -> str:
        return "Ιστορικό Παραστατικών"
