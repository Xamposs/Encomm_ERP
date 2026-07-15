"""Dashboard page — στατιστικά επισκόπησης."""

from qt_app.pages.base_page import BasePage


class DashboardPage(BasePage):
    """System overview with stat cards and critical alerts."""

    @classmethod
    def page_title(cls) -> str:
        return "Επισκόπηση Συστήματος"
