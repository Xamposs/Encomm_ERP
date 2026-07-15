"""Stock movements page — κινήσεις αποθέματος."""

from qt_app.pages.base_page import BasePage


class StockMovementsPage(BasePage):
    """Stock movement log and audit trail."""

    @classmethod
    def page_title(cls) -> str:
        return "Κινήσεις Αποθέματος"
