import customtkinter as ctk
import tkinter as tk
import os
import logging
import threading
from datetime import datetime
from tkinter import messagebox
from tkinter import ttk
from typing import Dict, List, Optional
from .base_view import BaseView


class StockMovementsView(BaseView):
    """Stock movement audit trail: filter by barcode/reason, paginate, export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.sm_page = 0
        self.sm_page_size = 50

        # ── Filter Bar ──
        self.sm_filter_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.sm_filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.sm_filter_bar.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(self.sm_filter_bar, text="Barcode:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0, 5))
        self.sm_barcode_entry = ctk.CTkEntry(
            self.sm_filter_bar, width=150, placeholder_text="Barcode...")
        self.sm_barcode_entry.grid(row=0, column=1, padx=(0, 15))

        self.sm_reason_var = tk.StringVar(value="")
        self.sm_reason_menu = ctk.CTkOptionMenu(
            self.sm_filter_bar,
            variable=self.sm_reason_var,
            values=["", "Εισαγωγή", "Πώληση", "Χειροκίνητη Ενημέρωση", "Επαναφορά"],
            width=150)
        self.sm_reason_menu.grid(row=0, column=2, padx=(0, 10))

        self.sm_search_btn = ctk.CTkButton(
            self.sm_filter_bar, text="🔍 Αναζήτηση",
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            command=self._reset_and_refresh)
        self.sm_search_btn.grid(row=0, column=3, sticky="w")

        # ── Treeview ──
        self.sm_table_container = ctk.CTkFrame(self)
        self.sm_table_container.grid(row=1, column=0, sticky="nsew")
        self.sm_table_container.grid_columnconfigure(0, weight=1)
        self.sm_table_container.grid_rowconfigure(0, weight=1)

        self.sm_scrollbar = ttk.Scrollbar(self.sm_table_container, orient="vertical")
        self.sm_scrollbar.grid(row=0, column=1, sticky="ns")

        self.sm_tree = ttk.Treeview(
            self.sm_table_container,
            columns=("timestamp", "barcode", "name", "old_stock", "new_stock", "diff", "reason", "source"),
            show="headings", height=20,
            yscrollcommand=self.sm_scrollbar.set, selectmode="browse")
        self.sm_tree.grid(row=0, column=0, sticky="nsew")
        self.sm_scrollbar.config(command=self.sm_tree.yview)

        for col, text, w, a in [
            ("timestamp", "Ημερομηνία", 160, "w"),
            ("barcode", "Barcode", 120, "w"),
            ("name", "Προϊόν", 200, "w"),
            ("old_stock", "Παλιό Στοκ", 80, "e"),
            ("new_stock", "Νέο Στοκ", 80, "e"),
            ("diff", "Διαφορά", 80, "e"),
            ("reason", "Αιτία", 140, "w"),
            ("source", "Πηγή", 100, "w"),
        ]:
            self.sm_tree.heading(col, text=text)
            self.sm_tree.column(col, width=w, anchor=a)

        self.sm_tree.configure(style="Treeview")

        # Color-code positive/negative diff
        self.sm_tree.tag_configure("positive", foreground="#34C759")
        self.sm_tree.tag_configure("negative", foreground="#FF3B30")

        # ── Pagination ──
        self.sm_pager = ctk.CTkFrame(self, fg_color="transparent")
        self.sm_pager.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.sm_pager.grid_columnconfigure(1, weight=1)

        self.sm_prev_btn = ctk.CTkButton(
            self.sm_pager, text="◀ Προηγ.", width=90, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._sm_prev_page)
        self.sm_prev_btn.grid(row=0, column=0, padx=(0, 5))

        self.sm_page_info = ctk.CTkLabel(
            self.sm_pager, text="", font=ctk.CTkFont(size=12),
            text_color=BaseView._subtle_text())
        self.sm_page_info.grid(row=0, column=1, sticky="e", padx=10)

        self.sm_next_btn = ctk.CTkButton(
            self.sm_pager, text="Επόμ. ▶", width=90, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._sm_next_page)
        self.sm_next_btn.grid(row=0, column=2, padx=(5, 0))

        # ── Export Bar ──
        self.sm_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.sm_export_bar.grid(row=3, column=0, sticky="ew", pady=(15, 0))

        self.sm_export_format = ctk.CTkOptionMenu(
            self.sm_export_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.sm_export_format.pack(side="left", padx=(0, 8))
        self.sm_export_btn = ctk.CTkButton(
            self.sm_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D",
            font=ctk.CTkFont(weight="bold"), command=self.export_movements)
        self.sm_export_btn.pack(side="left")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def _reset_and_refresh(self):
        self.sm_page = 0
        self.refresh()

    def _sm_next_page(self):
        if not self.winfo_ismapped():
            return
        self.sm_page += 1
        self.refresh()

    def _sm_prev_page(self):
        if not self.winfo_ismapped():
            return
        if self.sm_page > 0:
            self.sm_page -= 1
            self.refresh()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_movements(self):
        fmt = self.sm_export_format.get()
        is_csv = "csv" in fmt.lower()
        barcode = self.sm_barcode_entry.get().strip() or None
        reason = self.sm_reason_var.get().strip() or None

        rows = self.db_service.get_stock_movements(
            barcode=barcode, reason=reason, limit=5000)
        data = [
            [r.get("timestamp", ""), r.get("barcode", ""), r.get("product_name", ""),
             r.get("old_stock", 0), r.get("new_stock", 0), r.get("change_amount", 0),
             r.get("reason", ""), r.get("source", "")]
            for r in rows
        ]
        self._run_export(
            "StockMovements",
            ["Ημερομηνία", "Barcode", "Προϊόν", "Παλιό Στοκ", "Νέο Στοκ",
             "Διαφορά", "Αιτία", "Πηγή"],
            data, is_csv,
            txt_title="ΚΙΝΗΣΕΙΣ ΑΠΟΘΕΜΑΤΟΣ",
            txt_row_fmt="{0:<20} {1:<12} {2:<25} {3:>5} → {4:<5} ({5}) | {6}",
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.winfo_ismapped():
            return
        barcode = self.sm_barcode_entry.get().strip() or None
        reason = self.sm_reason_var.get().strip() or None

        def bg_fetch():
            try:
                rows = self.db_service.get_stock_movements(
                    barcode=barcode, reason=reason,
                    limit=self.sm_page_size, offset=self.sm_page * self.sm_page_size)
                self.after(0, self._safe_update_ui, rows)
            except Exception:
                logging.exception("Stock movements fetch failed")
                self.after(0, lambda: self._safe_update_ui([]))

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, rows: List[Dict]):
        if not hasattr(self, 'sm_tree') or self.sm_tree is None:
            return
        self.sm_tree.delete(*self.sm_tree.get_children())
        for r in rows:
            diff = r.get("change_amount", 0)
            tag = "positive" if diff > 0 else ("negative" if diff < 0 else ())
            self.sm_tree.insert("", "end", values=(
                r.get("timestamp", ""), r.get("barcode", ""),
                r.get("product_name", ""),
                r.get("old_stock", 0), r.get("new_stock", 0),
                diff, r.get("reason", ""), r.get("source", "")),
                tags=(tag,) if tag else ())

        self.sm_page_info.configure(
            text=f"Σελίδα {self.sm_page + 1}  |  {len(rows)} εγγραφές")
