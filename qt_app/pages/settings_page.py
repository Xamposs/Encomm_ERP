"""Settings page — ρυθμίσεις συστήματος."""

from qt_app.pages.base_page import BasePage


class SettingsPage(BasePage):
    """System configuration (VAT, thresholds, theme, backup, license)."""

    @classmethod
    def page_title(cls) -> str:
        return "Ρυθμίσεις Συστήματος"
