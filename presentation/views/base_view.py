import customtkinter as ctk
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from tkinter import messagebox


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

    # ------------------------------------------------------------------
    # Shared export helpers (CSV/TXT → Desktop, background-threaded)
    # ------------------------------------------------------------------

    @staticmethod
    def _export_path(prefix: str, ext: str) -> str:
        """Build a timestamped export destination on the user's Desktop."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(
            os.path.expanduser("~"), "Desktop", f"{prefix}_{ts}.{ext}")

    def _run_export(self, prefix: str, headers: list, rows: list,
                    is_csv: bool, txt_title: str = "",
                    txt_row_fmt: str = "{0}", success_msg: str = "") -> None:
        """Shared export pipeline used by every view's export action.

        Parameters
        ----------
        prefix     : filename prefix (e.g. ``"Inventory_Export"``).
        headers    : list of CSV column headers.
        rows       : list of row-lists; each inner list must align with
                     ``headers`` in both length and order.
        is_csv     : True → CSV (utf-8-sig); False → TXT (utf-8).
        txt_title  : banner title printed in the TXT header block.
        txt_row_fmt: ``str.format`` template applied to each row for TXT
                     output (defaults to space-joined values).
        success_msg: optional custom success message; a sensible default
                     is used when empty.
        """
        def _write():
            try:
                if is_csv:
                    dest = self._export_path(prefix, "csv")
                    lines = [",".join(str(h) for h in headers)]
                    for r in rows:
                        lines.append(
                            ",".join(self._csv_cell(v) for v in r))
                    encoding = "utf-8-sig"
                else:
                    dest = self._export_path(prefix, "txt")
                    bar = "=" * 60
                    lines = [bar, f"  ENCOMM — {txt_title}", bar,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                             f"Εγγραφές: {len(rows)}", "-" * 60]
                    for r in rows:
                        lines.append(txt_row_fmt.format(*r))
                    lines.append(bar)
                    encoding = "utf-8"
                with open(dest, "w", encoding=encoding) as f:
                    f.write("\n".join(lines))
                msg = success_msg or (
                    "Το αρχείο αποθηκεύτηκε στην Επιφάνεια Εργασίας!")
                self.after(0, lambda: messagebox.showinfo(
                    "Επιτυχής Εξαγωγή", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(
                    "Σφάλμα Εξαγωγής", str(e)))

        threading.Thread(target=_write, daemon=True).start()
