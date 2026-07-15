"""POS page — ταμείο / πωλήσεις."""

from qt_app.pages.base_page import BasePage


class POSPage(BasePage):
    """Point-of-sale interface."""

    @classmethod
    def page_title(cls) -> str:
        return "Ταμείο / Πωλήσεις (POS)"
