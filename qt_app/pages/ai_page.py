"""AI assistant page — AI βοηθός."""

from qt_app.pages.base_page import BasePage


class AIPage(BasePage):
    """Natural-language command interface via AIService."""

    @classmethod
    def page_title(cls) -> str:
        return "AI Βοηθός"
