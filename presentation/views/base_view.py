import customtkinter as ctk
from abc import ABC, abstractmethod


class BaseView(ctk.CTkFrame, ABC):
    """Base class for all ERP views. Provides db_service, config, and colour helpers."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        super().__init__(parent, **kwargs)
        self.db_service = db_service
        self.config = config

    @abstractmethod
    def refresh(self) -> None:
        """Each view must implement refresh to re-fetch and re-render its data."""
        ...

    # ------------------------------------------------------------------
    # Theme-aware colour helpers (static methods — single source of truth)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_dark_mode() -> bool:
        return ctk.get_appearance_mode() == "Dark"

    @staticmethod
    def _zebra_row(index: int) -> tuple:
        if index % 2 == 0:
            return ("#F0F2F5", "#16191E")
        return ("#E0E3E8", "#22252C")

    @staticmethod
    def _header_bg() -> tuple:
        return ("gray75", "gray20")

    @staticmethod
    def _header_fg() -> tuple:
        return ("gray30", "gray80")

    @staticmethod
    def _csv_cell(val) -> str:
        s = str(val)
        if "," in s or '"' in s or "\n" in s:
            s = '"' + s.replace('"', '""') + '"'
        return s

    @staticmethod
    def _nav_hover() -> tuple:
        return ("gray80", "gray25")

    @staticmethod
    def _nav_text() -> tuple:
        return ("gray40", "gray70")

    @staticmethod
    def _nav_active_bg() -> tuple:
        return ("#D0DAFF", "#252b36")

    @staticmethod
    def _nav_active_text() -> tuple:
        return ("#1D4ED8", "#3B82F6")

    @staticmethod
    def _stat_border_default() -> tuple:
        return ("#C8CCD4", "#2b303c")

    @staticmethod
    def _body_text() -> tuple:
        return ("gray20", "gray90")

    @staticmethod
    def _ttk_bg() -> str:
        return "#242424" if BaseView._is_dark_mode() else "#f0f0f0"

    @staticmethod
    def _ttk_fg() -> str:
        return "#ffffff" if BaseView._is_dark_mode() else "#000000"

    @staticmethod
    def _ttk_selected_bg() -> str:
        return "#3a3a3a" if BaseView._is_dark_mode() else "#d0d7ff"

    @staticmethod
    def _subtle_text() -> tuple:
        return ("gray55", "gray50")

    @staticmethod
    def _card_title_text() -> tuple:
        return ("gray45", "gray60")
